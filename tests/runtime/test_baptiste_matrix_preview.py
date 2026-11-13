"""Actual Matrix input, strict wire payloads and stored body/server observations."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_baptiste_combat as fixture
import test_server_snapshot as server_fixture

from ow174.game import observers, statescript
from ow174.game.baptiste import HERO
from ow174.game.bits import BitWriter
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.script import runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, Snapshot
from ow174.game.server_snapshot import snapshot_digest as server_digest

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "fixtures/behavior"
                       / "baptiste-matrix-preview-byte-v1.json").read_text(encoding="utf-8"))


def matrix_state(player):
    instance = next(i for i in player.body_script.component.instances.values()
                    if i.graph.index == CONTRACT["graph"])
    return instance.node_state(instance.graph.node(CONTRACT["node"]))


class BaptisteMatrixPreviewTests(unittest.TestCase):
    def test_actual_confirm_and_rebegin_encode_owner_and_observer_without_errors(self):
        run, player, _, _, fight, _, _ = fixture.BaptisteCombatTests().setup_fight()
        component = player.body_script.component
        for cycle in (1, 2):
            component.set(ULT_CHARGE, component.vars[ULT_COST].value())
            run.step({player: (0, 8192, 32)})
            state = matrix_state(player)
            self.assertTrue(state.active)
            self.assertEqual(state.counter, cycle)
            self.assertEqual(state.index, CONTRACT["state"])
            self.assertEqual(state.payload(), {CONTRACT["field"]: 0})
            self.check_wire_and_body(player, state)
            run.step({player: (0, 8192, 0)})
            with self.assertNoLogs("ow174.game.observers", level="ERROR"):
                run.step({player: (0, 8192, 1)})
            self.assertFalse(state.active)
            self.assertEqual(state.payload(), {CONTRACT["field"]: 1})
            self.check_wire_and_body(player, state)
            self.assertTrue(fight.baptiste.matrices)
            for _ in range(30):
                run.step({})

    def check_wire_and_body(self, player, state):
        for owner in (True, False):
            writer = BitWriter()
            if owner:
                statescript.write_payload(writer, state.node.cls, state.payload(), 0, 16, full=True)
            else:
                statescript.write_remote_payload(writer, state.node.cls, state.payload(), True)
            self.assertEqual(writer.count, 8)
            self.assertEqual(writer.value, state.confirm_byte)
        script = player.body_script
        first = snapshot_digest(export_body_vm(script))
        h0 = observers.full_frame(script, script.snapshot())
        h1, _, _ = script.delta(Snapshot(), set(), script.frame + 1, False)
        byte = state.confirm_byte
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("export ran VM")):
            state.confirm_byte ^= 1
            self.assertNotEqual(snapshot_digest(export_body_vm(script)), first)
            changed0 = observers.full_frame(script, script.snapshot())
            changed1, _, _ = script.delta(Snapshot(), set(), script.frame + 1, False)
            if state.active:
                self.assertNotEqual((h0.count, h0.value), (changed0.count, changed0.value))
                self.assertNotEqual((h1.count, h1.value), (changed1.count, changed1.value))
            else:
                # Full H0 omits an inactive state's payload. Stored VM export
                # still retains its confirm byte for the next activation.
                self.assertEqual((h0.count, h0.value), (changed0.count, changed0.value))
            state.confirm_byte = byte
            self.assertEqual(snapshot_digest(export_body_vm(script)), first)

    def test_full_server_export_distinguishes_matrix_byte_without_running_vm(self):
        helper = server_fixture.ServerSnapshotTests()
        session, peers, players = helper.scene()
        player, = players
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": HERO}))
        component = player.body_script.component
        component.set(ULT_CHARGE, component.vars[ULT_COST].value())
        peers[0].commands(player.body_script.frame + 1, 1, 32, pitch=8192)
        state = matrix_state(player)
        self.assertTrue(state.active)
        self.assertEqual(state.payload(), {CONTRACT["field"]: 0})
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("export ran VM")):
            initial = server_digest(helper.capture(session))
            state.confirm_byte = 1
            self.assertNotEqual(server_digest(helper.capture(session)), initial)
            state.confirm_byte = 0
            self.assertEqual(server_digest(helper.capture(session)), initial)
