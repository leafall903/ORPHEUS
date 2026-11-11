"""Actual defense casts on the reconstructed world missing-input policy."""

import unittest

import test_game_winston as winston_tests
import test_game_winston_deflect as genji_tests
import test_game_winston_matrix as matrix_tests
import test_game_winston_wraith as wraith_tests

from ow174.game import combat, protections
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command, CommandQueue, InputError
from ow174.game.content import SOLDIER, heroes
from ow174.game.projectiles import MAX_LEASE_FRAME


class DefenseClockTests(unittest.TestCase):
    def wraith(self):
        case = wraith_tests.WinstonWraithTests()
        case.setUp()
        return case, case.phase()

    def genji(self):
        case = genji_tests.WinstonDeflectTests()
        case.setUp()
        case.deflect()
        return case

    def matrix(self):
        matrix_tests.WinstonMatrixTests.setUpClass()
        case = matrix_tests.WinstonMatrixTests()
        case.setUp()
        case.activate()
        return case

    def world(self, case, ticks, *, skip=False):
        for _ in range(1 if skip else ticks):
            case.run.frame += ticks if skip else 1
            case.match.update(case.run.frame * combat.TICK, case.run.frame, frames=False)

    def test_wraith_original_wait_expires_then_cooldown_and_fresh_reuse_work_without_input(self):
        case, instance = self.wraith()
        actor = case.reaper
        script = actor.body_script
        counter = instance.state(1).counter
        last_packet = actor.commands.last_frame
        self.world(case, 180)
        self.assertTrue(protections.intangible(actor))
        self.world(case, 9)
        self.assertFalse(protections.intangible(actor))
        self.assertFalse(instance.state(3).active)
        self.world(case, 30)  # original0.4s exit recovery precedes the8s cooldown
        self.assertGreater(instance.vars[61].value(), 7.8)
        self.assertFalse(script.component.held)
        self.assertEqual(actor.commands.last_frame, last_packet)
        actor.take_commands([Command(script.frame + 1, buttons=8)])
        self.assertFalse(protections.intangible(actor))
        self.world(case, 515)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertEqual(instance.state(1).counter, counter)
        actor.take_commands([Command(script.frame + 1, buttons=8)])
        self.assertTrue(protections.intangible(actor))
        self.assertEqual(instance.state(1).counter, counter + 1)

    def test_sparse_world_expires_wraith_and_actual_winston_tesla_reaches_its_body(self):
        case, _instance = self.wraith()
        self.world(case, 625, skip=True)
        self.assertFalse(protections.intangible(case.reaper))
        case.step(1)
        self.assertAlmostEqual(case.health(case.reaper), 249.04)
        self.assertAlmostEqual(case.owner.body_script.component.entity_var(342).value(), .96)

    def test_stale_and_duplicate_packets_cannot_reopen_cooled_wraith_or_move_its_body(self):
        case, instance = self.wraith()
        actor = case.reaper
        old = actor.body_script.frame
        self.world(case, 800, skip=True)
        script = actor.body_script
        before = script.frame
        pose = tuple(actor.mover.position)
        for frame in (old + 1, old, before):
            actor.take_commands([Command(frame, buttons=8, forward=127)])
        self.assertEqual(script.frame, before)
        self.assertEqual(tuple(actor.mover.position), pose)
        self.assertEqual(instance.state(1).counter, 1)
        self.assertFalse(protections.intangible(actor))

    def test_current_input_window_remains_available_after_world_filled_history(self):
        case, instance = self.wraith()
        self.world(case, 800, skip=True)
        actor = case.reaper
        self.assertEqual(actor.body_script.frame, case.run.frame - 1)
        actor.take_commands([Command(case.run.frame, buttons=8)])
        self.assertEqual(actor.body_script.frame, case.run.frame)
        self.assertEqual(actor.body_script.world_frame_anchor, (case.run.frame, case.run.frame))
        self.assertTrue(protections.intangible(actor))
        self.assertEqual(instance.state(1).counter, 2)

    def test_future_frame_bursts_cannot_expire_wraith_or_advance_cooldown_and_poison_input_queue(self):
        case, instance = self.wraith()
        actor = case.reaper
        script = actor.body_script
        frame = script.frame
        queued = actor.commands.last_frame
        actor.take_commands([Command(frame + offset, buttons=8) for offset in range(1, 901)])
        self.assertEqual(script.frame, frame)
        self.assertEqual(actor.commands.last_frame, queued)
        self.assertTrue(protections.intangible(actor))
        self.assertEqual(instance.state(1).counter, 1)
        self.world(case, 1)
        actor.take_commands([Command(case.run.frame, buttons=0)])
        self.assertEqual(script.frame, case.run.frame)

    def test_winston_future_bursts_cannot_damage_spend_ammo_or_trigger_charged_actions(self):
        case = winston_tests.TeslaTests()
        case.setUp()
        actor = case.winston
        script = actor.body_script
        script.ult_seconds = 0
        frame = script.frame
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        for offset in range(1, 21):
            actor.take_commands([Command(frame + offset, buttons=1)])
        self.assertEqual(case.health(case.enemy), 200)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        script.set_ult_charge(1540)
        actor.take_commands([Command(frame + 200, buttons=8 | 16 | 32)])
        self.assertEqual(script.frame, frame)
        self.assertEqual(case.health(actor), 500)
        self.assertEqual(script.component.entity_var(342).value(), 1540)
        self.assertNotIn(actor.body, case.fight.barriers.actors)
        self.assertNotIn(actor.body, case.fight.winston_jump.flights)

    def test_winston_current_window_can_fire_once_but_future_packets_cannot_reanchor_it_forward(self):
        case = winston_tests.TeslaTests()
        case.setUp()
        actor = case.winston
        self.world(case, 1)
        actor.take_commands([Command(case.run.frame, buttons=1)])
        anchor = actor.body_script.world_frame_anchor
        self.assertAlmostEqual(case.health(case.enemy), 199.04)
        actor.take_commands([Command(case.run.frame + 1, buttons=1), Command(case.run.frame, buttons=1)])
        self.assertEqual(actor.body_script.world_frame_anchor, anchor)
        self.assertAlmostEqual(case.health(case.enemy), 199.04)
        case.step(1)
        self.assertAlmostEqual(case.health(case.enemy), 198.08)

    def test_winston_command_quanta_have_the_same_sixty_dps_and_no_extra_future_slice(self):
        for quanta in (8000, 16000, 32000):
            with self.subTest(quanta=quanta):
                case = winston_tests.TeslaTests()
                case.setUp()
                actor = case.winston
                actor.quanta = quanta
                case.match.switch_hero(actor, heroes()[0x02E0000000000009])
                actor.mover.position[:] = (0, 0, 0)
                actor.mover.body = None
                script = actor.body_script
                script.ult_seconds = 0
                start = script.frame
                self.world(case, 2)
                frames = 32000 // quanta
                actor.take_commands([Command(start + offset, buttons=1) for offset in range(1, frames + 1)])
                self.assertAlmostEqual(case.health(case.enemy), 200 - 60 * .032)
                actor.take_commands([Command(start + frames + 1, buttons=1)])
                self.assertAlmostEqual(case.health(case.enemy), 200 - 60 * .032)
                self.assertEqual(script.frame, start + frames)

    def test_deflect_expires_on_world_time_and_melee_admission_recovers(self):
        case = self.genji()
        old = case.genji.body_script.frame
        self.world(case, 80)
        self.assertTrue(case.state().active)
        self.world(case, 70)
        self.assertFalse(case.state().active)
        case.genji.take_commands([Command(old + 1, buttons=16)])
        self.assertFalse(case.state().active)
        case.step(0x800)
        for _ in range(40):
            case.step()
        self.assertEqual(case.health(case.genji), 170)

    def test_deflect_sparse_cooldown_expiry_allows_only_fresh_reactivation(self):
        case = self.genji()
        actor = case.genji
        counter = case.state().counter
        self.world(case, 700, skip=True)
        self.assertFalse(case.state().active)
        self.assertEqual(case.state().counter, counter)
        actor.take_commands([Command(case.run.frame, yaw=-32768, buttons=16)])
        self.assertTrue(case.state().active)
        self.assertEqual(case.state().counter, counter + 1)

    def test_matrix_idle_release_obeys_minimum_then_recovers_resource_and_fresh_input(self):
        case = self.matrix()
        actor, instance = case.mech, case.instance
        aim = actor.body_script.world.aim
        position = tuple(actor.mover.position)
        self.world(case, 10)
        self.assertTrue(instance.state(3).active)
        self.world(case, 35)
        self.assertFalse(instance.state(3).active)
        spent = instance.vars[87].value()
        self.assertLess(spent, 100)
        self.world(case, 500, skip=True)
        self.assertEqual(instance.vars[87].value(), 100)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertEqual(actor.body_script.world.aim, aim)
        self.assertEqual(tuple(actor.mover.position), position)
        actor.take_commands([Command(case.run.frame, yaw=-32768, buttons=2)])
        self.assertTrue(instance.state(3).active)

    def test_matrix_stale_rmb_and_future_burst_cannot_spend_recovered_resource(self):
        case = self.matrix()
        actor = case.mech
        old = actor.body_script.frame
        self.world(case, 600, skip=True)
        actor.take_commands([Command(old + 1, buttons=2), Command(case.run.frame + 200, buttons=2)])
        self.assertFalse(case.instance.state(3).active)
        self.assertEqual(case.instance.vars[87].value(), 100)
        actor.take_commands([Command(case.run.frame, yaw=-32768, buttons=2)])
        self.assertTrue(case.instance.state(3).active)

    def test_preexisting_real_helix_meets_matrix_before_idle_expiry_in_paced_and_sparse_world(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                case = self.matrix()
                case.move(case.owner, (-30, 1, 0))
                case.move(case.rifle, (0, 1, 0))
                case.move(case.mech, (0, 1, 25))
                for _ in range(5):
                    case.step(rifle=2)
                    if any(r.shooter is case.rifle for r in case.fight.rockets):
                        break
                self.assertTrue(any(r.shooter is case.rifle for r in case.fight.rockets))
                self.world(case, 1)  # establish the accepted player's flight at this endpoint
                self.assertTrue(any(r.shooter is case.rifle and r.last_tick is not None
                                    for r in case.fight.rockets))
                self.world(case, 220, skip=skip)
                self.assertFalse(case.instance.state(3).active)
                self.assertEqual(case.health(case.mech), 600)
                self.assertFalse(any(r.shooter is case.rifle for r in case.fight.rockets))
                self.assertEqual(case.rifle.client.messages(combat.HIT), [])

    def test_missing_input_discards_unposed_primary_reports_and_original_reload_completes(self):
        case = wraith_tests.WinstonWraithTests()
        case.setUp()
        case.step(defense=1)
        actor = case.reaper
        weapon = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x19)
        ammo = weapon.vars[53].value()
        health = case.health(case.owner)
        pose = tuple(actor.mover.position)
        self.world(case, 150, skip=True)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(case.health(case.owner), health)
        self.assertEqual(tuple(actor.mover.position), pose)
        self.assertFalse(actor.body_script.component.shots)
        actor.take_commands([Command(case.run.frame, buttons=0x400)])
        self.world(case, 150, skip=True)
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(case.health(case.owner), health)

    def test_world_anchor_is_causal_and_body_replacement_has_a_new_clock(self):
        case, _instance = self.wraith()
        actor = case.reaper
        script = actor.body_script
        digest = snapshot_digest(export_body_vm(script))
        original = script.world_frame_anchor
        script.world_frame_anchor = (original[0] + 1, original[1])
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), digest)
        script.world_frame_anchor = original
        self.assertEqual(snapshot_digest(export_body_vm(script)), digest)
        case.match.switch_hero(actor, heroes()[SOLDIER])
        replacement = actor.body_script
        self.assertTrue(script.retired)
        self.assertIsNone(replacement.world_frame_anchor)
        frame = replacement.frame
        self.world(case, 200, skip=True)
        self.assertEqual(replacement.frame, frame)
        self.assertFalse(protections.intangible(actor))

    def test_nondefault_command_quantum_uses_world_elapsed_time_instead_of_one_frame_per_tick(self):
        case, _instance = self.wraith()
        actor = case.reaper
        actor.quanta = 8000
        case.match.switch_hero(actor, heroes()[protections.REAPER])
        script = actor.body_script
        self.world(case, 1)
        actor.take_commands([Command(script.frame + 1, buttons=8)])
        self.assertTrue(protections.intangible(actor))
        self.assertEqual(script.frame_ms, 8)
        self.world(case, 200, skip=True)
        self.assertFalse(protections.intangible(actor))
        anchor_tick, anchor_frame = script.world_frame_anchor
        self.assertEqual(script.frame, anchor_frame + (case.run.frame - anchor_tick) * 2 - 1)

    def test_idle_fill_stops_at_wire_epoch_limit_and_boundary_still_requests_a_new_link(self):
        case, _instance = self.wraith()
        actor = case.reaper
        actor.commands.reset(last_frame=MAX_LEASE_FRAME - 3)
        case.match.switch_hero(actor, heroes()[protections.REAPER])
        script = actor.body_script
        self.world(case, 100, skip=True)
        self.assertEqual(script.frame, MAX_LEASE_FRAME - 1)
        frame = script.frame
        self.assertEqual(actor.take_commands([Command(MAX_LEASE_FRAME + 1)]), MAX_LEASE_FRAME + 1)
        self.assertEqual(script.frame, frame)


class CommandAdmissionTests(unittest.TestCase):
    def test_future_frames_do_not_poison_a_later_current_frame_in_the_same_packet(self):
        queue = CommandQueue()
        queue.reset(last_frame=1000)
        fresh, boundary = queue.before_limit(
            [Command(2000), Command(1001), Command(1002)], MAX_LEASE_FRAME, horizon=1002)
        self.assertEqual([c.frame for c in fresh], [1001, 1002])
        self.assertIsNone(boundary)
        self.assertEqual(queue.last_frame, 1002)

    def test_malformed_future_packet_is_rejected_atomically_and_lease_prefix_survives_horizon(self):
        queue = CommandQueue()
        queue.reset(last_frame=1000)
        with self.assertRaises(InputError):
            queue.before_limit([Command(2000), Command(True)], MAX_LEASE_FRAME, horizon=1001)
        self.assertEqual(queue.last_frame, 1000)
        queue.reset(last_frame=MAX_LEASE_FRAME - 2)
        fresh, boundary = queue.before_limit(
            [Command(MAX_LEASE_FRAME - 1), Command(MAX_LEASE_FRAME + 1)],
            MAX_LEASE_FRAME, horizon=MAX_LEASE_FRAME)
        self.assertEqual([c.frame for c in fresh], [MAX_LEASE_FRAME - 1])
        self.assertEqual(boundary, MAX_LEASE_FRAME + 1)


if __name__ == '__main__':
    unittest.main()
