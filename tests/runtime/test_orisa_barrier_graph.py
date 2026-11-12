"""Original0BD8 initialization/clock and causal VM storage; native engine remains open."""

import os
import unittest
from unittest.mock import patch

import test_orisa_barrier as fixture
import test_orisa_combat as combat_fixture
import test_orisa_supercharger as supercharger_fixture

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual extracted mesh required")
class OrisaBarrierGraphTests(unittest.TestCase):
    def test_actual_landing_starts_original_graph_and_bound_source_with_authored_pools(self):
        run, player, enemy, _, shield = fixture.BarrierTests().deploy()
        script, component = shield.script, shield.component
        self.assertEqual(script.graph.index, 0xBD8)
        self.assertIs(component.instances[script.id], script)
        self.assertEqual(component.entity, shield.entity)
        self.assertEqual(script.vars[40].value(), expr.Entity(shield.source_body))
        self.assertEqual(script.vars[586].value(), 600)
        self.assertEqual(script.vars[587].value(), 600)
        self.assertEqual(component.entity_var(567).value(), 20)
        self.assertEqual(component.entity_var(253).value(), shield.health)
        self.assertTrue(script.node_state(script.graph.node(0)).active)
        clock = script.node_state(script.graph.node(29))
        self.assertTrue(clock.active)
        remaining = component.entity_var(215).value()
        for _ in range(32):
            run.step({})
        self.assertAlmostEqual(remaining - component.entity_var(215).value(), 0.512, delta=0.002)
        self.assertEqual(script.vars[40].value(), expr.Entity(player.body))
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 580)
        self.assertEqual(component.entity_var(253).value(), 580)
        self.assertEqual(script.vars[586].value(), 600)

    def test_actual_depletion_retires_original_component_and_states(self):
        run, _, enemy, _, shield = fixture.BarrierTests().deploy()
        for _ in range(500):
            run.step({enemy: (32768, 0, FIRE)})
            if shield.health == 0:
                break
        self.assertEqual(shield.health, 0)
        self.assertTrue(shield.script.stopped)
        self.assertFalse(shield.component.instances)
        self.assertFalse(shield.script.queue)
        self.assertFalse(any(s.active for s in shield.script.states.values()))

    def test_natural_expiry_keeps_authored_half_second_phase_without_blocking_damage(self):
        run, player, enemy, _, shield = fixture.BarrierTests().deploy()
        manager = run.match.combat.orisa.barriers
        for _ in range(1500):
            run.step({})
            if shield.entity not in manager.active:
                break
        self.assertIs(manager.retiring[shield.entity], shield)
        self.assertFalse(shield.blocking())
        self.assertFalse(shield.script.stopped)
        wait = shield.script.states[shield.script.graph.node(30).state]
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 500)
        self.assertNotIn(shield, manager.opponents(enemy))
        fight = run.match.combat
        vitals = fight.vitals_of(combat_fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        run.step({enemy: (32768, 0, FIRE)})
        self.assertLess(vitals.total, before)
        self.assertIs(manager.retiring[shield.entity], shield)
        for _ in range(40):
            run.step({})
            if shield.entity not in manager.retiring:
                break
        self.assertNotIn(shield.entity, manager.retiring)
        self.assertTrue(shield.script.stopped)
        self.assertEqual(wait.ended_at - wait.start, 500)
        self.assertFalse(shield.component.instances)

    def test_world_gap_advances_original_finish_wait_without_reactivating_collision(self):
        run, _, enemy, _, shield = fixture.BarrierTests().deploy()
        manager = run.match.combat.orisa.barriers
        manager.update(shield.until)
        self.assertNotIn(shield.entity, manager.active)
        self.assertIn(shield.entity, manager.retiring)
        self.assertNotIn(shield, manager.opponents(enemy))
        before = snapshot_digest(export_component_vm(shield.component))
        wait = shield.script.states[shield.script.graph.node(30).state]
        self.assertTrue(any(e.state is wait and e.time == wait.start + 500 for e in shield.script.queue))
        wait.ms += 1  # isolated storage visibility probe; restore before advancing
        self.assertNotEqual(snapshot_digest(export_component_vm(shield.component)), before)
        wait.ms -= 1
        manager.update(shield.until + 40)
        self.assertFalse(manager.active)
        self.assertFalse(manager.retiring)
        # An overdue event is dispatched on the next update, rather than
        # backdating its observed end. Its authored deadline remains500ms.
        self.assertGreaterEqual(wait.ended_at - wait.start, 500)

    def test_active_barrier_world_and_hidden_clock_storage_are_exported(self):
        _, _, _, _, shield = fixture.BarrierTests().deploy()
        before = snapshot_digest(export_component_vm(shield.component))
        clock = shield.script.node_state(shield.script.graph.node(29))
        original = clock.cur
        clock.cur -= 0.25  # isolated causal-storage test, not a combat/lifetime acceptance input
        self.assertNotEqual(snapshot_digest(export_component_vm(shield.component)), before)
        clock.cur = original
        self.assertEqual(snapshot_digest(export_component_vm(shield.component)), before)

    def test_live_deployable_capture_does_not_import_modules_while_observing(self):
        _, _, _, _, shield = fixture.BarrierTests().deploy()
        with patch("builtins.__import__", side_effect=AssertionError("import during capture")):
            export_component_vm(shield.component)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_active_supercharger_world_also_exports_and_tracks_hidden_graph_state(self):
        _, _, _, _, device = supercharger_fixture.SuperchargerTests().deploy()
        before = snapshot_digest(export_component_vm(device.component))
        # The same typed-world adapter must include an actual live device VM,
        # rather than only passing snapshots with pending/synthetic deployables.
        device.script.vars[199].base -= 1
        self.assertNotEqual(snapshot_digest(export_component_vm(device.component)), before)
