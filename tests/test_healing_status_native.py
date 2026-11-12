"""Original status VMs on the existing sole received-healing host."""
import unittest
from ow174.game import combat
from ow174.game.content import heroes
from ow174.game.script import graph
from test_game_combat import Run

ANA = 0x02E000000000013B

class NativeHealingStatusTests(unittest.TestCase):
    def scene(self):
        run = Run(names=("Ana", "Target"))
        ana, target = run.players[:2]
        self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        return run, fight, ana, target, body

    def test_original_application_runs_child_filter_and_expires_on_same_host(self):
        run, fight, ana, target, body = self.scene()
        self.assertTrue(fight.healing.available())
        before = fight.vitals_of(body).total
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        self.assertIs(item.component.world.match.combat.healing, fight.healing)
        self.assertIsNone(item.component.fault)
        self.assertTrue(any(i.graph.index == 0x0C72 for i in item.component.instances.values()))
        self.assertLess(fight.vitals_of(body).total, before)
        self.assertEqual(fight.healing.factor(body), 0)
        self.assertEqual(fight.healing.scale(body.entity), 0)
        for _ in range(251):
            run.step({})
        self.assertEqual(fight.healing.factor(body), 1)
        self.assertEqual(fight.healing.scale(body.entity), 1)

    def test_retire_clears_actual_status_components(self):
        run, fight, ana, target, body = self.scene()
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        fight.healing.retire(body.entity)
        self.assertFalse(item.component.instances)
        self.assertFalse(fight.healing.applied)
        self.assertNotIn(body.entity, fight.healing.filter_history)

    def test_component_storage_tracks_original_status_timer_and_rejects_unknown(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest, BodyVMSnapshotError
        run, fight, ana, target, body = self.scene()
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        before = snapshot_digest(export_component_vm(item.component))
        timer = next(event for instance in item.component.instances.values()
                     for event in instance.queue if event.kind == 1)
        original = timer.time
        timer.time += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(item.component)), before)
        timer.time = original
        self.assertEqual(snapshot_digest(export_component_vm(item.component)), before)
        item.component.world.unregistered = True
        with self.assertRaises(BodyVMSnapshotError):
            export_component_vm(item.component)
        del item.component.world.unregistered
        self.assertEqual(snapshot_digest(export_component_vm(item.component)), before)

    def test_native_filter_sparse_expiry_preserves_same_shield_ledger(self):
        values = []
        for sparse in (False, True):
            run, fight, ana, target, body = self.scene()
            self.assertTrue(run.match.switch_hero(target, heroes()[0x02E0000000000068]))
            run.step({})
            body = next(t for t in fight._targets() if t.owner is target)
            fight.healing.impact(ana, body)
            self.assertEqual(fight.healing.factor(body), 0)
            through = run.frame + 313
            if sparse:
                run.frame = through
                run.match.update(through * combat.TICK, through)
            else:
                for _ in range(313):
                    run.step({})
            self.assertEqual(fight.healing.factor(body), 1)
            values.append(fight.vitals_of(body).total)
        self.assertAlmostEqual(values[0], values[1], places=4)
        self.assertGreater(values[0], 360)
        self.assertLess(values[0], 380)

    def test_actual_server_captures_status_vm_and_filter_history_causally(self):
        import test_server_snapshot as storage
        from ow174.game.server_snapshot import snapshot_digest, ServerSnapshotError
        helper = storage.ServerSnapshotTests()
        session, peers, players = helper.scene(two=True)
        ana, recipient = players[:2]
        self.assertTrue(session.apply("switch_hero", {"account": ana.account_lo, "hero": ANA}))
        fight = ana.match.combat
        body = next(t for t in fight._targets() if t.owner is recipient)
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        before = snapshot_digest(helper.capture(session))
        original = item.last_frame
        item.last_frame += 1
        self.assertNotEqual(snapshot_digest(helper.capture(session)), before)
        item.last_frame = original
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
        item.component.world.unregistered = True
        with self.assertRaises(ServerSnapshotError):
            helper.capture(session)
        del item.component.world.unregistered
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
