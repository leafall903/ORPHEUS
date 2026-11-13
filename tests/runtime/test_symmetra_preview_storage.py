"""Actual E/Q preview bytes remain visible in strict body and server storage."""
import unittest
from unittest.mock import patch

import test_server_snapshot as server_fixture

from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_body_vm, snapshot_digest
from ow174.game.script import runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import ServerSnapshotError
from ow174.game.server_snapshot import snapshot_digest as server_digest
from ow174.game.symmetra import HERO


class SymmetraPreviewStorageTests(unittest.TestCase):
    def test_actual_e_and_q_bytes_change_both_exports_and_unknown_fields_stay_rejected(self):
        for button, graph, positions in ((16, 0x1571, (69, 72)), (32, 0x156B, (57,))):
            with self.subTest(graph=graph):
                helper = server_fixture.ServerSnapshotTests()
                session, peers, players = helper.scene()
                owner, = players
                self.assertTrue(session.apply('switch_hero', {'account': 1, 'hero': HERO}))
                script = owner.body_script
                component = script.component
                if button == 32:
                    component.set(ULT_CHARGE, component.vars[ULT_COST].value())
                peers[0].commands(script.frame + 1, 1, button, pitch=8192)
                instance = next(i for i in component.instances.values() if i.graph.index == graph)
                states = [instance.node_state(instance.graph.node(pos)) for pos in positions]
                self.assertTrue(all(state.active for state in states))
                self.assertTrue(all(state.payload() == {'m_B4802CE3': 0} for state in states))
                with patch.object(runtime.Component, 'run_now', side_effect=AssertionError('export ran VM')):
                    original_body = snapshot_digest(export_body_vm(script))
                    original_server = server_digest(helper.capture(session))
                    for state in states:
                        # Observation causality only; the separate actual-input
                        # lifecycle regression proves the original typed confirm.
                        state.placement_byte = 1
                        self.assertNotEqual(snapshot_digest(export_body_vm(script)), original_body)
                        self.assertNotEqual(server_digest(helper.capture(session)), original_server)
                        state.placement_byte = 0
                        self.assertEqual(snapshot_digest(export_body_vm(script)), original_body)
                        self.assertEqual(server_digest(helper.capture(session)), original_server)
                    states[0].unproved_palette_override = 0
                    try:
                        with self.assertRaises(BodyVMSnapshotError):
                            export_body_vm(script)
                        with self.assertRaises((BodyVMSnapshotError, ServerSnapshotError)):
                            helper.capture(session)
                    finally:
                        del states[0].unproved_palette_override
