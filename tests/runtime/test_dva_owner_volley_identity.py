"""Actual controlled-mech firing must survive owner delta generation on ACK."""
import json
import unittest
from pathlib import Path

import test_server_journal as journal

from ow174.game import dva
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class DvaOwnerVolleyIdentityTests(unittest.TestCase):
    def test_actual_fusion_commands_and_ack_keep_real_mech_identity(self):
        contract = json.loads((Path(__file__).resolve().parents[2] /
                               'fixtures/behavior/dva-owner-volley-identity-v1.json').read_text())
        case = journal.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, first_hero=dva.HERO)
        pilot = players[0]
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        self.assertFalse(hasattr(mech.body_script, 'hero'))
        self.assertEqual(mech.body_script.component.world.hero, dva.HERO)

        for index in range(contract['input']['neutral_frames']):
            now = 100.944 + index * .016
            for peer, player in zip(peers, players, strict=True):
                peer.commands(player.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        start_frame = mech.body_script.frame
        for buttons, count in contract['input']['commands']:
            now += count * .016
            session.tick(now)
            peers[0].commands(pilot.body_script.frame + 1, count, buttons, now=now)
            peers[1].commands(players[1].body_script.frame + 1, count, 0, now=now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        delivery = pilot.client._bot_scripts[mech.entity]
        self.assertGreater(delivery.stream.base_last, 1)
        self.assertGreater(delivery.stream.cmfd, start_frame)
        self.assertLessEqual(delivery.stream.cmfd, mech.body_script.frame)
        self.assertIs(pilot.controlled_mech, mech)
        self.assertEqual((mech.owner, mech.source_body), (pilot.entity, pilot.body))
        self.assertIsNone(mech.body_script.component.fault)

    def test_actual_normal_charge_q_return_binding_is_storable_before_ack(self):
        case = journal.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, first_hero=dva.HERO)
        pilot = players[0]
        mech = pilot.controlled_mech
        component = mech.body_script.component
        now = 100.944
        # Exercise current normal charging, including its reconstructed passive
        # curve. Do not set charge, stop a state or force a possession target.
        for _ in range(400):
            if (component.entity_var(ULT_CHARGE).value() or 0) >= component.entity_var(ULT_COST).value():
                break
            now += .016 * 16
            session.tick(now)
            peers[0].commands(pilot.body_script.frame + 1, 16, 0, now=now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertGreaterEqual(component.entity_var(ULT_CHARGE).value(),
                                component.entity_var(ULT_COST).value())
        now += .016
        peers[0].commands(pilot.body_script.frame + 1, 1, 32, now=now)
        for _ in range(10):
            now += .016
            session.tick(now)
            if pilot.controlled_mech is None:
                break  # capture the actual returned binding before its ACK
            peers[0].commands(pilot.body_script.frame + 1, 1, 0, now=now)
        self.assertIsNone(pilot.controlled_mech)
        self.assertTrue(mech.detached_bomb)
        self.assertEqual(pilot.match._possession(pilot)[44], [pilot.body] * 3)
        self.assertIn(pilot.entity, pilot.client._pending_possessions)
        self.assertEqual(len(session._state_digest()), 64)


if __name__ == '__main__':
    unittest.main()
