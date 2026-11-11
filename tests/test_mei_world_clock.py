"""Scoped reconstructed world/input clock: no independent immunity deadline."""

import os
import unittest

import test_mei_combat as fixtures
import test_mei_cryo as cryo
import test_mei_phases as phases

from ow174.game import combat, mei, phase_defenses
from ow174.game.commands import Command, CommandQueue, InputError
from ow174.game.content import heroes


class MeiWorldClockTests(unittest.TestCase):
    def idle(self, run, ticks, *, jump=False):
        if jump:
            run.frame += ticks
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            for _ in range(ticks):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)

    def test_wraith_and_fade_expire_without_commands_and_skipped_ticks_match(self):
        for hero in (phases.REAPER, phases.MOIRA):
            outcomes = []
            for jump in (False, True):
                with self.subTest(hero=hero, jump=jump):
                    f = phases.MeiPhaseTests()
                    run, _owner, enemy, _target = f.scene(hero)
                    instance = f.cast(run, enemy)
                    position = enemy.mover.position
                    self.idle(run, 300, jump=jump)
                    self.assertFalse(phase_defenses.active(enemy))
                    self.assertEqual(enemy.mover.position, position)
                    self.assertGreater(instance.vars[61].value(), 0.)
                    outcomes.append((enemy.body_script.frame, instance.vars[61].value()))
            self.assertEqual(outcomes[0], outcomes[1])

    def test_cryo_healing_end_cooldown_and_charge_continue_without_commands(self):
        results = []
        for jump in (False, True):
            run, owner, _enemy, _target, health = cryo.CryoTests().duel()
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0.)
            health.damage(200.)
            run.step({owner: (0, 0, 8)})
            self.idle(run, 300, jump=jump)
            self.assertFalse(mei.protected(owner))
            self.assertEqual(health.total, 250.)
            self.assertAlmostEqual(owner.body_script.component.entity_var(342).value(), 200., delta=.001)
            instance = mei.cryo_instance(owner)
            self.assertGreater(instance.vars[61].value(), 11.)
            results.append((health.total, instance.vars[61].value()))
        self.assertEqual(results[0], results[1])

    def test_future_and_committed_late_commands_cannot_restart_or_move_old_windows(self):
        f = phases.MeiPhaseTests()
        run, _owner, enemy, _target = f.scene(phases.REAPER)
        f.cast(run, enemy)
        old_frame = enemy.body_script.frame
        self.idle(run, 30)
        committed = enemy.body_script.frame
        position, history = enemy.mover.position, len(enemy.mover.history)
        enemy.take_commands([Command(old_frame + 1, buttons=8, forward=127)])
        self.assertEqual(enemy.body_script.frame, committed)
        self.assertEqual(enemy.mover.position, position)
        self.assertEqual(len(enemy.mover.history), history)
        queue = enemy.commands.last_frame
        enemy.take_commands([Command(committed + 10000, buttons=8)])
        self.assertEqual(enemy.commands.last_frame, queue)
        self.assertEqual(enemy.body_script.frame, committed)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        enemy.take_commands([Command(run.frame)])
        self.assertEqual(len(enemy.mover.history), history + 1)
        self.assertEqual(enemy.body_script.frame, run.frame)

    def test_nonzero_command_epoch_and_20ms_quanta_use_world_anchor(self):
        run, _owner, enemy, _target = fixtures.MeiCombatTests().duel()
        enemy.quanta = 20000
        enemy.commands.reset(last_frame=50000)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[phases.REAPER]))
        self.assertEqual(enemy.body_script.world_frame_anchor, (run.frame, 50000))
        self.idle(run, 2)
        enemy.take_commands([Command(50001, buttons=8)])
        self.assertTrue(phase_defenses.active(enemy))
        self.idle(run, 210, jump=True)
        self.assertFalse(phase_defenses.active(enemy))
        self.assertEqual(enemy.body_script.frame, 50000 + 212 * 16 // 20 - 1)

    def test_no_missing_pose_can_create_ordinary_weapon_or_melee_damage(self):
        run, owner, _enemy, target = fixtures.MeiCombatTests().duel()
        before = run.match.combat.vitals_of(target).total
        run.step({owner: (0, 0, 1)})
        position = owner.mover.position
        self.idle(run, 80, jump=True)
        self.assertEqual(owner.mover.position, position)
        self.assertEqual(run.match.combat.vitals_of(target).total, before)
        self.assertFalse(owner.client.messages(combat.HIT))

    def test_previously_admitted_blizzard_windup_releases_once_without_more_input(self):
        run, owner, _enemy, _target = fixtures.MeiCombatTests().duel()
        owner.body_script.set_ult_charge(1610.)
        x, y, z = owner.mover.position
        yaw, pitch = fixtures.aim(run.match.combat._eye(owner), (x, y, z + 5.))
        allocator = run.match.combat.next_bot
        run.step({owner: (yaw, pitch, 32)})
        self.idle(run, 45)
        manager = run.match.combat.blizzard
        self.assertEqual(len(manager.flights) + len(manager.areas), 1)
        self.idle(run, 400, jump=True)
        self.assertFalse(manager.flights)
        self.assertFalse(manager.areas)
        self.assertEqual(run.match.combat.next_bot, allocator + 1)

    def test_horizon_rejection_is_atomic_and_does_not_poison_future_queue(self):
        queue = CommandQueue()
        queue.reset(last_frame=1000)
        got, boundary = queue.before_limit([Command(1001), Command(100000)], 1000000, horizon=1001)
        self.assertEqual([c.frame for c in got], [1001])
        self.assertIsNone(boundary)
        self.assertEqual(queue.last_frame, 1001)
        with self.assertRaises(InputError):
            queue.before_limit([Command(1002), Command(-1)], 1000000, horizon=1002)
        self.assertEqual(queue.last_frame, 1001)
        got, _ = queue.before_limit([Command(1002)], 1000000, horizon=1002)
        self.assertEqual([c.frame for c in got], [1002])

    def test_existing_icicle_observes_phase_at_historical_contact_before_gap_end(self):
        f = phases.MeiPhaseTests()
        run, owner, enemy, target = f.scene(phases.REAPER)
        f.cast(run, enemy)
        yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                  (target.feet[0], target.feet[1] + .9, target.feet[2]))
        for frame in range(50):
            run.step({owner: (yaw, pitch, 2 if frame < 20 else 0)})
            if run.match.combat.rockets:
                break
        self.assertTrue(run.match.combat.rockets)
        run.step({})
        self.assertTrue(phase_defenses.active(enemy))
        before = run.match.combat.vitals_of(target).total
        self.idle(run, 250, jump=True)
        self.assertFalse(phase_defenses.active(enemy))
        self.assertEqual(run.match.combat.vitals_of(target).total, before)
        self.assertFalse(run.match.combat.rockets)

    def test_blizzard_no_input_jump_matches_stepped_launch_contact_and_damage(self):
        outcomes = []
        for jump in (False, True):
            run, owner, _enemy, target = fixtures.MeiCombatTests().duel()
            owner.body_script.set_ult_charge(1610.)
            x, y, z = owner.mover.position
            angle = fixtures.aim(run.match.combat._eye(owner), (x, y, z + 5.))
            run.step({owner: (*angle, 32)})
            self.idle(run, 180, jump=jump)
            area = next(iter(run.match.combat.blizzard.areas.values()))
            outcomes.append((area.position, area.starts, area.ends,
                             run.match.combat.vitals_of(target).total))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_deflect_ends_without_commands_and_cannot_protect_a_later_icicle(self):
        import test_mei_deflect as deflect

        from ow174.game import genji_deflect

        run, owner, genji, target = deflect.DeflectTests().scene()
        self.idle(run, 160, jump=True)
        self.assertIsNone(genji_deflect.active(run.match.combat, genji))
        before = run.match.combat.vitals_of(target).total
        fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
        self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.)

    def test_body_storage_captures_world_anchor_that_changes_input_horizon(self):
        from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest

        _run, owner, _enemy, _target = fixtures.MeiCombatTests().duel()
        script = owner.body_script
        anchor = script.world_frame_anchor
        before = snapshot_digest(export_body_vm(script))
        script.world_frame_anchor = (anchor[0] + 1, anchor[1])
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)
        script.world_frame_anchor = anchor
        self.assertEqual(snapshot_digest(export_body_vm(script)), before)

    def test_zarya_native_shield_flags_expire_with_world_without_commands(self):
        import test_mei_shields as shields

        f = shields.ShieldTests()
        run, owner, zarya, _receiver = f.scene()
        f.cast(run, zarya)
        self.idle(run, 150, jump=True)
        self.assertFalse(run.match.combat.shields.shielded(zarya))
        self.assertFalse(zarya.body_script.component.entity_var(8440).value())
        target = f.target(run, zarya)
        before = run.match.combat.vitals_of(target).total
        self.assertEqual(run.match.combat._damage(owner, target, 75., False, None, run.frame), 75.)
        self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.)

    @unittest.skipUnless(os.environ.get('ORPHEUS_DVA_MECH_DATA'), 'explicit mech supplement required')
    def test_dva_matrix_cannot_remain_active_without_more_input(self):
        import test_mei_matrix as matrix

        from ow174.game import dva_matrix

        f = matrix.MatrixTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        run, _owner, dva, _instance, angle = f.scene()
        run.step({dva: (*angle, 2)})
        self.assertIsNotNone(dva_matrix.active(run.match.combat, dva))
        self.idle(run, 160, jump=True)
        self.assertIsNone(dva_matrix.active(run.match.combat, dva))
