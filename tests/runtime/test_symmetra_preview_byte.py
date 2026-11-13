"""Actual placement inputs retain the original uint8 begin/confirm lifecycle."""
import json
import unittest
from pathlib import Path

import test_game_moira_photon as photon_fixture
import test_symmetra_portals as portal_fixture
import test_symmetra_static_placement as placement_fixture


class PreviewByteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/symmetra-preview-byte-v1.json'
        cls.contract = json.loads(path.read_text(encoding='utf-8'))

    def check_action(self, button, graph, nodes):
        if graph == 0x156B:
            fixture = photon_fixture.MoiraPhotonTests()
            run, _, owner, instance = fixture.arena()
            fixture.preview(run, owner, 8192)
            yaw, pitch = -32768, 8192
        else:
            fixture = placement_fixture.StaticPlacementTests()
            run, owner = fixture.scene()
            instance, _ = fixture.preview(run, owner, button, graph, nodes[0])
            yaw, pitch = 0, 0
        expected = self.contract['expected']
        states = [instance.node_state(instance.graph.node(node)) for node in nodes]
        for state in states:
            self.assertTrue(state.active)
            self.assertEqual(state.payload(), {expected['payload_field']: expected['begin_byte']})
            self.assertIs(type(state.payload()[expected['payload_field']]), int)
        run.step({owner: (yaw, pitch, self.contract['input']['confirm_button'])})
        for state in states:
            self.assertFalse(state.active)
            self.assertEqual(state.payload(), {expected['payload_field']: expected['typed_confirm_byte']})
        return run, owner, instance, states

    def test_actual_portal_inputs_publish_both_original_preview_bytes(self):
        run, owner, _instance, states = self.check_action(
            self.contract['input']['portal_button'], 0x1571, self.contract['expected']['portal_nodes'])
        pair, = run.match.combat.portals.pairs
        portal_fixture.TeleporterTests.until(run, pair.ready_at)
        run.step({owner: (0, 0, self.contract['input']['portal_button'])})
        self.assertFalse(run.match.combat.portals.pairs)
        portal_fixture.TeleporterTests.until(run, run.match.combat.now + 10.1)
        run.step({owner: (0, 0, self.contract['input']['portal_button'])})
        for state in states:
            self.assertEqual(state.counter, 2)
            self.assertTrue(state.active)
            self.assertEqual(state.payload(), {'m_B4802CE3': 0})

    def test_actual_photon_inputs_publish_original_preview_byte(self):
        run, owner, _instance, states = self.check_action(
            self.contract['input']['photon_button'], 0x156B, self.contract['expected']['photon_nodes'])
        manager = run.match.combat.photon_barriers
        for _ in range(26):
            if manager.actors:
                break
            run.step({})
        actor, = manager.actors.values()
        manager.damage(actor, actor.health)
        for _ in range(self.contract['input']['post_break_idle_frames']):
            run.step({})
        photon_fixture.MoiraPhotonTests().preview(run, owner, 8192)
        self.assertEqual(states[0].counter, 2)
        self.assertTrue(states[0].active)
        self.assertEqual(states[0].payload(), {'m_B4802CE3': 0})
