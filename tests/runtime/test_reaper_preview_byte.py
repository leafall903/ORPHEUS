"""Original Shadow Step placement byte on the real command-confirm path."""
import json
import unittest
from pathlib import Path
from unittest.mock import call, patch

from ow174.game import reaper, statescript
from ow174.game.bits import BitWriter
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.body_vm_snapshot import snapshot_digest as body_digest
from ow174.game.server_snapshot import export_server_state
from ow174.game.server_snapshot import snapshot_digest as server_digest
from tests.runtime import test_game_reaper as scenarios
from tests.runtime import test_server_journal as journal


class ReaperPreviewByteTests(unittest.TestCase):
    def assert_byte(self, state, expected):
        self.assertEqual(state.payload(), {'m_B4802CE3': expected})
        self.assertIs(type(state.payload()['m_B4802CE3']), int)
        for owner in (True, False):
            out = BitWriter()
            if owner:
                statescript.write_payload(out, state.node.cls, state.payload(), 0, 16, full=False)
            else:
                statescript.write_remote_payload(out, state.node.cls, state.payload(), False)
            self.assertEqual((out.count, out.getvalue()), (8, bytes([expected])))

    def test_actual_shadow_typed_confirm_and_rebegin(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              'fixtures/behavior/reaper-preview-byte-v1.json').read_text(encoding='utf-8'))
        run, owner, _enemy, _target = scenarios.ReaperTests().scenario(team_mode=True)
        instance = next(i for i in owner.body_script.component.instances.values()
                        if i.graph.index == reaper.SHADOW_GRAPH)
        state = instance.state(0)
        self.assertEqual([instance.graph.index, state.node.pos, state.index, state.node.remote],
                         fixture['context'])
        self.assertEqual(state.node.field('m_6647ED22')['$'], fixture['typedInputClass'])
        self.assert_byte(state, 0)
        command = (0, 5000)
        run.step({owner: (*command, 16)})
        for _ in range(8):
            run.step({owner: (*command, 0)})
        self.assertTrue(state.active)
        self.assert_byte(state, 0)
        with patch.object(state, 'plug_enter', wraps=state.plug_enter) as typed:
            run.step({owner: (*command, 1)})
        self.assertEqual(typed.call_args_list.count(call('m_6647ED22')), 1)
        self.assertFalse(state.active)
        self.assertTrue(instance.state(32).active)
        self.assert_byte(state, 1)
        # Exercise the same original state lifecycle; do not shorten cooldown.
        state.begin()
        self.assert_byte(state, 0)
        state.end(False)
        # Missing/wrongly typed producer data must still fail strict encoding.
        state.placement_byte = True
        self.assertEqual(state.payload(), {'m_B4802CE3': True})
        with self.assertRaises(ValueError):
            statescript.write_remote_payload(BitWriter(), state.node.cls, state.payload(), False)

    def test_actual_body_and_full_server_snapshots_preserve_confirmation_byte(self):
        helper = journal.ServerJournalTests()
        session, sent = helper.make()
        peers, players = helper.connected(session, sent, two=False, first_hero=journal.REAPER)
        owner = players[0]
        script = owner.body_script
        instance = next(i for i in script.component.instances.values()
                        if i.graph.index == reaper.SHADOW_GRAPH)
        state = instance.state(0)
        peers[0].commands(script.frame + 1, 1, 16, pitch=5000, now=100.944)
        self.assertTrue(state.active)
        self.assert_byte(state, 0)

        def capture_server():
            return export_server_state(session.server, archives=session._matches.values(),
                                       collision_provider=session._collision_reads, assets=session.registry)

        before_body = body_digest(export_body_vm(script))
        before_server = server_digest(capture_server())
        # Same actual typed input and same concrete state, before producer completion.
        self.assertTrue(state.plug_enter('m_6647ED22'))
        self.assert_byte(state, 1)
        self.assertNotEqual(before_body, body_digest(export_body_vm(script)))
        self.assertNotEqual(before_server, server_digest(capture_server()))
