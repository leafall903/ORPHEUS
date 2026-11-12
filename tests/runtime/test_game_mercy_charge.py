"""Original0B8B passive charge in actual Mercy Match command processing.

Mode-category/role and retained body->controller hosts are reconstructed;
damage/heal/boost awards and complete native mode/possession remain open.
"""

import unittest
from unittest.mock import patch

import test_game_mercy_staff as staff_tests

from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.script import expr, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class MercyChargeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = staff_tests.MercyStaffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.mercy = self.fixture.mercy
        self.script = self.mercy.body_script
        self.component = self.script.component
        self.charge_graph = next(i for i in self.component.instances.values() if i.graph.index == 0x0B8B)

    def step(self, count):
        self.fixture.step(count, buttons=0)

    def charge(self):
        return self.component.entity_var(ULT_CHARGE).value()

    def test_original_wait_is_active_and_awards_five_after_one_second(self):
        self.assertTrue(self.component.entity_var(2426).value())
        self.assertTrue(self.charge_graph.state(1).active)
        self.step(60)
        self.assertEqual(self.charge(), 0)
        self.step(3)
        self.assertEqual(self.charge(), 5)
        self.step(63)
        self.assertEqual(self.charge(), 10)

    def test_native_related_entity_is_typed_and_uses_declared_controller(self):
        cfg = self.charge_graph.graph.nodes[1].field("m_condition")["m_configVars"][0]
        self.assertEqual(self.charge_graph.evaluate(cfg), expr.Entity(self.mercy.entity))
        self.assertNotEqual(self.charge_graph.evaluate(cfg), expr.Entity(self.mercy.body))
        self.mercy.gone = True
        self.step(70)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.charge_graph.evaluate(cfg), expr.Entity(0))
        self.mercy.gone = False
        self.step(64)  # relation is re-polled in the first frame; wait starts there
        self.assertEqual(self.charge(), 5)

    def test_original_default_multipliers_are_applied_by_graph(self):
        self.component.set(9532, 2.0)
        self.component.set(12918, 0.25)
        self.step(63)
        self.assertEqual(self.charge(), 2.5)

    def test_cost_cap_stops_timer_and_spending_restarts_it(self):
        cost = self.component.entity_var(ULT_COST).value()
        self.script.set_ult_charge(cost - 2)
        self.step(63)
        self.assertEqual(self.charge(), cost)
        self.assertFalse(self.charge_graph.state(1).active)
        self.step(70)
        self.assertEqual(self.charge(), cost)
        self.script.set_ult_charge(0)
        self.step(60)
        self.assertEqual(self.charge(), 0)
        self.step(4)
        self.assertEqual(self.charge(), 5)

    def test_duplicate_command_cannot_award_or_advance_wait(self):
        self.step(63)
        before = self.component.now
        for _ in range(20):
            self.mercy.take_commands([Command(self.script.frame)])
        self.assertEqual(self.component.now, before)
        self.assertEqual(self.charge(), 5)

    def test_idle_world_ticks_preserve_passive_cadence_without_advancing_input(self):
        run = self.fixture.run
        start = run.frame
        command_state = self.script.frame, self.component.frame, self.component.now, self.mercy.mover.last_command

        run.match.update((start + 60) * 0.016, start + 60)
        self.assertEqual(self.charge(), 0)
        run.match.update((start + 62) * 0.016, start + 62)
        self.assertEqual(self.charge(), 5)
        run.match.update((start + 625) * 0.016, start + 625)
        self.assertEqual(self.charge(), 50)
        self.assertEqual((self.script.frame, self.component.frame, self.component.now,
                          self.mercy.mover.last_command), command_state)

        run.match.update((start + 625) * 0.016, start + 625)
        self.mercy.take_commands([Command(start + 624), Command(start + 625)])
        self.assertEqual(self.charge(), 50)
        self.assertFalse(hasattr(self.script.world, "pending_ult_credits"))

    def test_late_ultimate_cannot_spend_future_passive_credit(self):
        run = self.fixture.run
        self.script.set_ult_charge(self.component.entity_var(ULT_COST).value() - 5)
        due = next(event.time for event in self.charge_graph.queue if event.kind == runtime.TIMER and event.state.node.pos == 3)
        frame = due // self.script.frame_ms
        run.match.update((frame + 2) * 0.016, frame + 2)
        self.assertEqual(self.charge(), 1820)
        self.assertEqual(self.script.world.pending_ult_credits, ((due, 5),))

        self.mercy.take_commands([Command(frame - 1, buttons=0x20)])
        self.assertFalse(self.component.entity_var(842).value())
        self.assertEqual(self.charge(), 1820)
        self.mercy.take_commands([Command(frame, buttons=0x20)])
        self.assertTrue(self.component.entity_var(842).value())
        self.assertEqual(self.charge(), 0)

    def test_large_idle_jump_resumes_pending_passive_events_without_repeating_credit(self):
        run = self.fixture.run
        start = self.script.frame
        self.component.set(12918, 0.125)
        due = next(event.time for event in self.charge_graph.queue
                   if event.kind == runtime.TIMER and event.state.node.pos == 3)
        jump = start + 68_750  # 1100 seconds at the retail frame rate.
        last_time = (jump + 1) * self.script.frame_ms - 1
        expected = (1 + (last_time - due) // 1000) * 0.625

        run.match.update(jump * 0.016, jump)
        self.assertGreater(self.charge(), 0)
        self.assertLess(self.charge(), expected)
        run.match.update((jump + 1) * 0.016, jump + 1)
        self.assertEqual(self.charge(), expected)
        run.match.update((jump + 1) * 0.016, jump + 1)
        self.assertEqual(self.charge(), expected)
        self.assertEqual(self.script.frame, start)

    def test_mode_message_disables_and_restores_authored_eligibility(self):
        for identifier in (0x16, 0x18):
            with self.subTest(identifier=identifier):
                runtime.send_message(self.component, 0x024000000000002D,
                                     {0x29: expr.Asset(0x0D80000000000000 | identifier)}, self.mercy.entity)
                self.step(70)
                self.assertFalse(self.component.entity_var(2426).value())
                self.assertEqual(self.charge(), 0)
        runtime.send_message(self.component, 0x024000000000002D,
                             {0x29: expr.Asset(0)}, self.mercy.entity)
        self.step(63)
        self.assertTrue(self.component.entity_var(2426).value())
        self.assertEqual(self.charge(), 5)

    def test_health_updates_do_not_overwrite_authored_eligibility(self):
        self.script.set_health(0, 200)
        self.assertTrue(self.component.entity_var(2426).value())
        self.script.set_health(200, 200)
        self.assertTrue(self.component.entity_var(2426).value())
        runtime.send_message(self.component, 0x024000000000002D,
                             {0x29: expr.Asset(0x0D80000000000016)}, self.mercy.entity)
        self.step(1)
        self.script.set_health(0, 200)
        self.assertFalse(self.component.entity_var(2426).value())

    def test_original_suspension_aborts_wait_then_restarts_a_full_second(self):
        self.step(40)
        self.component.set(842, True)
        self.step(75)
        self.assertEqual(self.charge(), 0)
        self.assertFalse(self.charge_graph.state(1).active)
        self.component.set(842, False)
        self.step(60)
        self.assertEqual(self.charge(), 0)
        self.step(4)
        self.assertEqual(self.charge(), 5)

    def test_actual_alive_ally_cannot_be_resurrected_when_eligibility_is_true(self):
        self.assertTrue(self.component.entity_var(2426).value())
        self.fixture.step(120, buttons=0x10)
        self.assertFalse(self.fixture.fight.vitals_of(self.fixture.target).dead)
        res = next(i for i in self.component.instances.values() if i.graph.index == 0x00CA)
        self.assertEqual(res.find_var(0, 61).value(), 0)

    def test_retained_dead_body_charges_then_new_body_starts_a_new_timer(self):
        target = next(t for t in self.fixture.fight._targets() if t.owner is self.mercy)
        self.fixture.fight._damage(self.fixture.enemy, target, 1000, False, None, self.fixture.run.frame)
        self.step(70)
        self.assertTrue(self.fixture.fight.vitals_of(target).dead)
        self.assertTrue(self.component.entity_var(2426).value())
        self.assertEqual(self.charge(), 5)  # Match retains the declared controller relation
        self.fixture.match.switch_hero(self.mercy, self.mercy.hero)
        fresh = self.mercy.body_script
        self.assertIsNot(fresh, self.script)
        self.assertEqual(fresh.component.entity_var(ULT_CHARGE).value(), 0)
        self.step(60)
        self.assertEqual(fresh.component.entity_var(ULT_CHARGE).value(), 0)
        self.step(4)
        self.assertEqual(fresh.component.entity_var(ULT_CHARGE).value(), 5)

    def test_original_wait_and_authored_eligibility_are_observable_without_running_queries(self):
        self.step(20)
        self.assertEqual(dict(self.script.replicated_vars())[2426].value(), True)
        with patch.object(type(self.script.world), "native",
                          side_effect=AssertionError("capture executed query")):
            before = snapshot_digest(export_body_vm(self.script))
            self.charge_graph.state(1).start += 1
            changed = snapshot_digest(export_body_vm(self.script))
            self.charge_graph.state(1).start -= 1
            restored = snapshot_digest(export_body_vm(self.script))
        self.assertNotEqual(before, changed)
        self.assertEqual(before, restored)


class MercyChargeSnapshotTests(unittest.TestCase):
    def test_scope_history_and_pending_credit_are_in_server_storage(self):
        import test_server_snapshot as snapshot_tests
        from ow174.game.server_snapshot import snapshot_digest as server_digest
        from ow174.game.script import charge, regeneration

        fixture = snapshot_tests.ServerSnapshotTests()
        session, _, players = fixture.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": regeneration.MERCY})
        script = player.body_script
        capture = lambda: server_digest(fixture.capture(session))
        before = capture()
        script.world.scope_host.frame += 1
        self.assertNotEqual(capture(), before)
        script.world.scope_host.frame -= 1
        self.assertEqual(capture(), before)
        charge.set_pending_credits(script.world, ((script.component.now + 1000, 5),))
        self.assertNotEqual(capture(), before)
        charge.set_pending_credits(script.world, ())
        self.assertEqual(capture(), before)


if __name__ == "__main__":
    unittest.main()
