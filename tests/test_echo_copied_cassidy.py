"""Actual Echo Q -> copied01F0 Roll motion, resources and lifecycle; native limits explicit."""
import math
import unittest
from unittest.mock import patch

import test_echo_combat as echo_tests
import test_echo_movement as movement
import test_mccree_flashbang as flash
import test_server_snapshot as snapshots
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import copied_roll
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
from ow174.game.content import heroes
from ow174.game.mover import MOVEMENT_MOD, Mover, mover_data

CASSIDY = 0x02E0000000000042


class EchoCopiedCassidyTests(unittest.TestCase):
    step = staticmethod(movement.EchoMovementTests.step)

    def session(self):
        run, echo, enemy, _, angles = echo_tests.EchoPrimaryTests().session()
        run.match.switch_hero(enemy, heroes()[CASSIDY], preserve_pose=True)
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        self.assertEqual(echo.hero.guid, CASSIDY)
        self.assertTrue(run.match.combat.echo_duplicates.active(echo))
        for _ in range(25):
            self.step(run, echo)
        instance = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x1F0)
        return run, echo, enemy, instance

    @staticmethod
    def scope(instance):
        return next(s for s in instance.states.values() if s.node.cls == MOVEMENT_MOD
                    and any(m.get('m_A9561CA0') == 19 for m in s.node.field('m_BC5E91CF') or ()))

    def test_idle_roll_uses_next_pose_moves_six_metres_refills_and_cannot_apply_duplicate_input_twice(self):
        run, echo, _, instance = self.session()
        weapon = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x1D2)
        self.step(run, echo, 1)
        for _ in range(40):
            self.step(run, echo)
        self.assertEqual(weapon.vars[53].value(), 5)
        before = tuple(echo.mover.position)
        self.step(run, echo, 8)
        phase = self.scope(instance)
        self.assertEqual(tuple(echo.mover.position), before)
        self.assertTrue(phase.active)
        self.assertTrue(echo.body_script.world.velocity_requests)
        self.step(run, echo)
        self.assertAlmostEqual(echo.mover.velocity[2], 15, delta=.001)
        position = tuple(echo.mover.position)
        echo.take_commands([Command(run.frame, buttons=8)])
        self.assertEqual(tuple(echo.mover.position), position)
        self.assertFalse(echo.body_script.world.velocity_requests)
        while phase.active:
            self.step(run, echo)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.assertAlmostEqual(echo.mover.position[2]-before[2], 6, delta=.02)
        self.assertGreater(instance.vars[61].value(), 0)

    def test_authored_directional_expressions_use_input_and_yaw(self):
        for forward, right, yaw, axis, sign in (
                (127, 0, 0, 2, 1), (-127, 0, 0, 2, -1),
                (0, 127, 0, 0, -1), (127, 0, 16384, 0, 1)):
            with self.subTest(forward=forward, right=right, yaw=yaw):
                run, echo, _, instance = self.session()
                before = tuple(echo.mover.position)
                self.step(run, echo, 8, forward=forward, right=right, yaw=yaw)
                before = tuple(echo.mover.position)  # the command pose precedes its queued next-frame delta
                phase = self.scope(instance)
                self.step(run, echo, yaw=yaw)
                self.assertAlmostEqual(echo.mover.velocity[axis], sign*15, delta=.01)
                while phase.active:
                    self.step(run, echo, yaw=yaw)
                self.assertAlmostEqual(echo.mover.position[axis]-before[axis], sign*6, delta=.02)

    def test_actual_roll_sweeps_static_wall(self):
        run, echo, _, instance = self.session()
        x, y, z = echo.mover.position
        collision = World(box(x-20, x+20, y-1, y, z-20, z+20)
                          + wall(x-10, z+2, x+10, z+2, y0=y, y1=y+6))
        run.match.collision_world = collision
        echo.mover.use(world=collision)
        self.step(run, echo, 8)
        phase = self.scope(instance)
        for _ in range(30):
            self.step(run, echo)
        self.assertFalse(phase.active)
        self.assertGreater(echo.mover.position[2]-z, 1)
        self.assertLess(echo.mover.position[2]-z, 1.7)

    def test_actual_cooldown_early_tap_expires_and_fresh_roll_reuses_native_scope(self):
        run, echo, _, instance = self.session()
        self.step(run, echo, 8)
        phase = self.scope(instance)
        for _ in range(40):
            self.step(run, echo)
        counter = phase.counter
        self.assertFalse(phase.active)
        self.assertGreater(instance.vars[61].value(), 2)
        self.step(run, echo, 8)
        while instance.vars[61].value() > 0:
            self.step(run, echo)
        for _ in range(2):
            self.step(run, echo)
        self.assertEqual(phase.counter, counter)
        self.assertFalse(phase.active)
        self.step(run, echo, 8)
        self.assertGreater(phase.counter, counter)
        self.assertTrue(phase.active)
        self.assertTrue(run.match.combat.echo_duplicates.active(echo))

    def test_pending_actual_roll_request_changes_snapshot_and_retirement_clears_it(self):
        run, echo, _, _ = self.session()
        self.step(run, echo, 8)
        script = echo.body_script
        self.assertTrue(script.world.velocity_requests)
        digest = snapshot_digest(export_body_vm(script))
        pending = script.world.velocity_requests.copy()
        script.world.velocity_requests.clear()
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), digest)
        script.world.velocity_requests[:] = pending  # snapshot sensitivity, not motion evidence
        self.assertTrue(run.match.switch_hero(echo, heroes()[echo_tests.ECHO], preserve_pose=True))
        self.assertTrue(script.retired)
        self.assertFalse(script.world.velocity_requests)
        self.assertFalse(echo.body_script.world.velocity_requests)
        self.assertLess(math.hypot(echo.mover.velocity[0], echo.mover.velocity[2]), .01)

    def test_actual_foreign_flashbang_interrupts_roll_and_clears_pending_motion(self):
        run, echo, enemy, instance = self.session()
        self.step(run, echo, 8)
        phase = self.scope(instance)
        self.assertTrue(phase.active)
        flash.FlashbangTests().cast(run, enemy, echo)
        self.assertTrue(run.match.combat.is_stunned(echo))
        self.assertFalse(phase.active)
        self.assertFalse(echo.body_script.world.velocity_requests)
        position = tuple(echo.mover.position)
        end = run.match.combat.stuns[echo.body]
        while run.frame < end-1:
            self.step(run, echo, 8, forward=127, right=127)
            self.assertEqual(tuple(echo.mover.position), position)
        self.step(run, echo)
        self.assertFalse(run.match.combat.is_stunned(echo))
        for _ in range(20):
            self.step(run, echo, forward=127)
        self.assertGreater(echo.mover.position[2], position[2]+1)

    def test_copied_deadeye_locks_and_hits_an_off_axis_enemy_player(self):
        run, echo, enemy, _ = self.session()
        fight = run.match.combat
        x, y, z = echo.mover.position
        enemy.mover = Mover((x+4, y, z+8), world=run.match.collision_world,
                            data=mover_data(enemy.hero.body))
        script = echo.body_script
        component = script.component
        script.ult_seconds = 0
        cost = component.entity_var(338).value()
        script.set_ult_charge(cost)
        run.step({echo: (0, 0, 32)})
        for _ in range(125):
            run.step({})
        child = next(i for i in component.instances.values() if i.graph.index == 0x01EE)
        self.assertEqual(child.vars[536].value().id, enemy.body)
        self.assertGreater(child.vars[476].value(), 0)
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertIn("'children'", repr(export_body_vm(script)))
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        before = vitals.total
        run.step({echo: (0, 0, 1)})
        for _ in range(90):
            run.step({})
        self.assertLess(vitals.total, before)
        self.assertFalse(any(i.graph.index == 0x01EE for i in component.instances.values()))
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertTrue(fight.echo_duplicates.active(echo))
        run.step({echo: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertFalse(any(i.graph.index == 0x01EE for i in component.instances.values()))

    def test_copied_deadeye_real_wall_pauses_charge_then_target_can_be_hit(self):
        run, echo, enemy, _ = self.session()
        fight = run.match.combat
        component = echo.body_script.component
        echo.body_script.ult_seconds = 0
        echo.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({echo: (0, 0, 32)})
        for _ in range(100):
            run.step({})
        child = next(i for i in component.instances.values() if i.graph.index == 0x01EE)
        collision = run.match.collision_world
        x, y, z = echo.mover.position
        run.match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                                          + wall(x-10, z+2, x+10, z+2, y0=y, y1=y+6))
        for _ in range(8):
            run.step({})
        charge = child.vars[476].value()
        self.assertEqual(component.entity_var(5874).value(), ())
        for _ in range(40):
            run.step({})
        self.assertEqual(child.vars[476].value(), charge)
        self.assertIn(child.id, component.instances)
        run.match.collision_world = collision
        for _ in range(30):
            run.step({})
        self.assertGreater(child.vars[476].value(), charge)
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        before = vitals.total
        run.step({echo: (0, 0, 1)})
        for _ in range(100):
            run.step({})
        self.assertLess(vitals.total, before)
        self.assertFalse(any(i.graph.index == 0x01EE for i in component.instances.values()))

    def test_copied_deadeye_cancel_stun_and_copy_expiry_clear_targets_without_firing(self):
        for end in ("cancel", "stun", "expiry"):
            with self.subTest(end=end):
                run, echo, enemy, _ = self.session()
                fight = run.match.combat
                expiry = end == "expiry"
                if expiry:
                    until = fight.echo_duplicates.states[echo.entity].expires
                    while until - fight.now > 2:
                        run.step({})
                script = echo.body_script
                component = script.component
                script.ult_seconds = 0
                script.set_ult_charge(component.entity_var(338).value())
                run.step({echo: (0, 0, 32)})
                for _ in range(75):
                    run.step({})
                self.assertTrue(any(i.graph.index == 0x01EE for i in component.instances.values()))
                target = next(t for t in fight._targets() if t.owner is enemy)
                vitals = fight.vitals_of(target)
                before = vitals.total
                if end == "cancel":
                    run.step({echo: (0, 0, 2)})
                elif end == "stun":
                    flash.FlashbangTests().cast(run, enemy, echo)
                    self.assertTrue(fight.is_stunned(echo))
                for _ in range(100):
                    run.step({})
                self.assertFalse(any(i.graph.index == 0x01EE for i in component.instances.values()))
                self.assertEqual(vitals.total, before)
                self.assertEqual(fight.echo_duplicates.active(echo), not expiry)
                self.assertEqual(script.retired, expiry)

    def test_copied_deadeye_can_lock_beyond_its_damage_range(self):
        run, echo, enemy, _ = self.session()
        fight = run.match.combat
        x, y, z = echo.mover.position
        enemy.mover = Mover((x, y, z+150), world=run.match.collision_world,
                            data=mover_data(enemy.hero.body))
        script = echo.body_script
        component = script.component
        script.ult_seconds = 0
        script.set_ult_charge(component.entity_var(338).value())
        run.step({echo: (0, 0, 32)})
        for _ in range(125):
            run.step({})
        self.assertTrue(any(i.graph.index == 0x01EE for i in component.instances.values()))
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        before = vitals.total
        with patch.object(fight, "_shoot", wraps=fight._shoot) as emitted:
            run.step({echo: (0, 0, 1)})
            for _ in range(100):
                run.step({})
            graphs = {call.args[2].instance.graph.index for call in emitted.call_args_list}
            self.assertIn(0x01EE, graphs)
        self.assertEqual(vitals.total, before)
        self.assertFalse(any(i.graph.index == 0x01EE for i in component.instances.values()))

    def test_copied_flashbang_flies_hits_rejects_cooldown_and_reuses(self):
        run, echo, enemy, _ = self.session()
        fight = run.match.combat
        instance = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x1E9)
        victim = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(victim)
        initial = vitals.total
        angles = aim(fight._eye(echo), fight._eye(enemy))
        run.step({echo: (*angles, 16)})
        grenade = None
        for _ in range(65):
            run.step({})
            grenade = next((r for r in fight.rockets if r.shooter is echo), grenade)
            if fight.is_stunned(enemy):
                break
        self.assertIsNotNone(grenade)
        self.assertTrue(grenade.detonate_on_expiry)
        self.assertEqual(grenade.gravity, 20)
        self.assertEqual(vitals.total, initial - 25)
        ability = next(s for s in instance.states.values() if s.node.pos == 2)
        for _ in range(65):
            if ability.remaining() > 0:
                break
            run.step({})
        self.assertEqual(instance.vars[472].value(), 10)
        self.assertGreater(ability.remaining(), 9)
        run.step({echo: (*angles, 16)})
        for _ in range(50):
            run.step({})
            self.assertFalse(any(r.shooter is echo for r in fight.rockets))
        self.assertEqual(vitals.total, initial - 25)
        for _ in range(700):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertFalse(fight.is_stunned(enemy))
        flash.FlashbangTests().cast(run, echo, enemy)
        self.assertEqual(vitals.total, initial - 50)
        self.assertTrue(fight.is_stunned(enemy))

    def test_real_copy_expiry_during_roll_retires_motion_and_restores_echo(self):
        run, echo, _, instance = self.session()
        copies = run.match.combat.echo_duplicates
        while copies.states[echo.entity].expires-run.match.combat.now > .12:
            self.step(run, echo)
        old = echo.body_script
        self.step(run, echo, 8)
        self.assertTrue(self.scope(instance).active)
        for _ in range(20):
            self.step(run, echo)
            if echo.hero.guid == echo_tests.ECHO:
                break
        self.assertEqual(echo.hero.guid, echo_tests.ECHO)
        self.assertFalse(copies.active(echo))
        self.assertTrue(old.retired)
        self.assertFalse(old.world.velocity_requests)
        self.assertFalse(echo.body_script.world.velocity_requests)
        speed = math.hypot(echo.mover.velocity[0], echo.mover.velocity[2])
        for _ in range(20):
            self.step(run, echo)
            self.assertFalse(old.world.velocity_requests)
            self.assertFalse(echo.body_script.world.velocity_requests)
            self.assertFalse(any(m.index in (17, 18, 19) for m in echo.mover.mods.values()))
        # Existing Duplicate policy preserves pose/velocity; inertia need not vanish.
        self.assertLess(math.hypot(echo.mover.velocity[0], echo.mover.velocity[2]), speed)

    def test_actual_empty_magazine_roll_refill_does_not_leave_weapon_reload_latched(self):
        run, echo, _, instance = self.session()
        weapon = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x1D2)
        for _ in range(240):
            self.step(run, echo, 1, yaw=32768)
            if weapon.vars[53].value() == 0:
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        for _ in range(5):
            self.step(run, echo)
        self.step(run, echo, 8)
        self.assertTrue(self.scope(instance).active)
        for _ in range(35):
            self.step(run, echo)
        self.assertEqual(weapon.vars[53].value(), 6)
        self.step(run, echo, 1, yaw=32768)
        for _ in range(10):
            self.step(run, echo)
        self.assertEqual(weapon.vars[53].value(), 5)

    def test_real_incoming_echo_fire_breaks_copied_body_during_roll_and_retires_old_motion(self):
        run, echo, enemy, instance = self.session()
        fight = run.match.combat
        run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        for _ in range(25):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is echo)
        low, high = target.segment()
        centre = tuple((a+b)*.5 for a, b in zip(low, high, strict=True))
        incoming = aim(fight._eye(enemy), centre)
        while fight.vitals_of(target).total > 51:
            before = fight.vitals_of(target).total
            run.step({enemy: (*incoming, 1)})
            for _ in range(35):
                run.step({})
            self.assertEqual(before-fight.vitals_of(target).total, 51)
        self.assertGreater(fight.vitals_of(target).total, 0)
        old = echo.body_script
        run.step({echo: (0, 0, 8), enemy: (*incoming, 1)})
        self.assertTrue(self.scope(instance).active)
        for _ in range(20):
            run.step({})
            if echo.hero.guid == echo_tests.ECHO:
                break
        self.assertEqual(echo.hero.guid, echo_tests.ECHO)
        self.assertFalse(fight.echo_duplicates.active(echo))
        self.assertTrue(old.retired)
        self.assertFalse(old.world.velocity_requests)
        self.assertFalse(echo.body_script.world.velocity_requests)
        self.assertFalse(fight.is_dead(echo))

    def test_actual_pending_roll_is_causal_in_passive_encrypted_server_capture(self):
        # Meter preparation is explicit; this is passive capture, not journal replay.
        observer = snapshots.ServerSnapshotTests()
        session, peers, players = observer.scene(two=True)
        player, enemy = players
        session.apply('switch_hero', {'account':enemy.account_lo, 'hero':CASSIDY})
        session.apply('switch_hero', {'account':player.account_lo, 'hero':echo_tests.ECHO})
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        peers[0].commands(player.body_script.frame+1, 1, 32)
        self.assertEqual(player.hero.guid, CASSIDY)
        self.assertTrue(player.match.combat.echo_duplicates.active(player))
        peers[0].commands(player.body_script.frame+1, 25, 0)
        peers[0].commands(player.body_script.frame+1, 1, 8)
        world = player.body_script.world
        pending = world.velocity_requests.copy()
        self.assertTrue(pending)
        with patch.object(copied_roll, 'apply_velocity', side_effect=AssertionError('passive capture')):
            first = snapshots.snapshot_digest(observer.capture(session))
            world.velocity_requests.clear()
            self.assertNotEqual(snapshots.snapshot_digest(observer.capture(session)), first)
            world.velocity_requests[:] = pending

    def test_echo_and_real_copied_cassidy_use_authored_standing_and_crouched_eye_heights(self):
        for copied, standing, crouched in ((False, 1.7, 1.1), (True, 1.425, .9975)):
            with self.subTest(copied=copied):
                if copied:
                    run, actor, _, _ = self.session()
                else:
                    run, actor, _, _, _ = echo_tests.EchoPrimaryTests().session()
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is actor)
                self.assertAlmostEqual(fight._eye(actor)[1]-actor.mover.position[1], standing)
                self.assertAlmostEqual(target.eye, standing)
                for _ in range(4):
                    self.step(run, actor, CROUCH)
                self.assertTrue(actor.mover.crouched)
                target = next(t for t in fight._targets() if t.owner is actor)
                self.assertAlmostEqual(fight._eye(actor)[1]-actor.mover.position[1], crouched)
                self.assertAlmostEqual(target.eye, crouched)

    def test_copied_cassidy_low_cover_uses_native_camera_height(self):
        for crouch, height, loss in ((False, 1.49, 0), (False, 1.2, 70), (True, 1.2, 0)):
            with self.subTest(crouch=crouch, wall_height=height):
                run, actor, enemy, _ = self.session()
                run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
                x, y, z = actor.mover.position
                geometry = World(box(x-20, x+20, y-1, y, z-20, z+20)
                                 + wall(x-10, z+1.5, x+10, z+1.5, y0=y, y1=y+height))
                run.match.collision_world = geometry
                actor.mover.use(world=geometry)
                held = CROUCH if crouch else 0
                for _ in range(4):
                    self.step(run, actor, held)
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                before = run.match.combat.vitals_of(target).total
                self.step(run, actor, held|1)
                for _ in range(15):
                    self.step(run, actor, held)
                self.assertEqual(before-run.match.combat.vitals_of(target).total, loss)

    def test_copied_primary_implicit_critical_is_scoped_and_fan_explicit_one_is_preserved(self):
        for button, expected in ((1, 140), (2, 50)):
            with self.subTest(button=button):
                run, actor, enemy, _ = self.session()
                run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
                fight = run.match.combat
                x, y, z = actor.mover.position
                enemy.mover = Mover((x,y,z+1.5), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                for _ in range(25):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is enemy)
                point = (target.feet[0], target.feet[1]+target.eye-.12, target.feet[2])
                attack = aim(fight._eye(actor), point)
                before = fight.vitals_of(target).total
                for _ in range(20):
                    run.step({actor:(*attack,button)})
                    if fight.vitals_of(target).total < before:
                        break
                self.assertEqual(before-fight.vitals_of(target).total, expected)
