"""D.Va's current/view body must be the mech, while primary stays the pilot."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dva_delivery_created_body as created
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import body_entities, world
from ow174.game.combat import Vitals
from ow174.game.content import find_hero
from ow174.game.link import ACK, KIND_CONTROL, Packet, ReceiveWindow
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from tests.runtime.test_game_packet_budget import connection, decoded
from tests.runtime.test_game_repeated_lifecycles import wire_frame


class DvaPossessionHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    def test_pick_publishes_created_mech_as_current_and_view_target(self):
        run = Run(KANEZAKA_DM, ("Pilot", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        pilot = run.players[0]
        with patch.object(pilot.client, "queue_possession", wraps=pilot.client.queue_possession) as queue:
            self.assertTrue(run.match.switch_hero(pilot, find_hero("dva")))
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        update, dependency = queue.call_args.args
        self.assertEqual(dependency, mech.entity)
        with patch.object(world, "update", wraps=world.update) as encode:
            update.build(0)
        self.assertEqual(encode.call_args.args[1][44], [mech.entity, mech.entity, pilot.body])
        self.assertTrue(any(row.entity == mech.entity and row.op == world.OP_CREATE
                            for row in pilot.client.entities))

    def test_owner_binding_waits_for_mech_create_after_pilot_create_ack(self):
        _, peer, pilot, _ = connection()
        pilot.has_body = True
        peer._own_controller_acked = True
        peer._combat_notices[pilot.body] = None
        mech_entity = 0xC000100F
        binding = world.EntityUpdate(pilot.entity, world.OP_UPDATE)
        peer.queue_possession(binding, mech_entity)
        creation = world.EntityUpdate(mech_entity, world.OP_CREATE)
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/dva-possession-create-order-v1.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        records = {"binding": binding, "mech_create": creation}
        for step, expected in zip(
            contract["input"]["schedule"], contract["expected"]["selected"], strict=True,
        ):
            with self.subTest(step=step):
                peer.entities = [records[name] for name in step["queued"]]
                if step["mech_create_acked"]:
                    peer._combat_notices[mech_entity] = None
                self.assertEqual(peer._ready_entity_records(), [records[name] for name in expected])

    def test_preselected_initial_controller_waits_for_the_owned_mech(self):
        _, peer, pilot, _ = connection()
        match = pilot.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(pilot, find_hero("dva")))
        updates = match.spawn_updates(pilot)
        peer.queue_entities(updates)
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        controller = next(item for item in updates if item.entity == pilot.entity)
        mech_create = next(item for item in updates if item.entity == mech.entity)
        selected = peer._ready_entity_records()
        if controller in selected:
            self.assertLess(selected.index(mech_create), selected.index(controller))
        peer.entities = [controller]
        peer._combat_notices[pilot.body] = None
        self.assertNotIn(controller, peer._ready_entity_records())
        peer._combat_notices[mech.entity] = None
        self.assertIn(controller, peer._ready_entity_records())

    def test_preselected_wire_binding_follows_mech_create_even_after_packet_loss(self):
        for drop_create in (False, True):
            with self.subTest(drop_create=drop_create):
                server, peer, pilot, cipher = connection()
                match = pilot.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                match.tick = 100
                self.assertTrue(match.switch_hero(pilot, find_hero("dva")))
                peer.queue_entities(match.spawn_updates(pilot))
                pilot.spawned = True
                mech = pilot.controlled_mech
                known = set()
                window = ReceiveWindow()
                dropped = bound = False
                for sequence in range(1, 13):
                    match.tick += 1
                    peer.send_frame(match.tick, 10.0)
                    packet = decoded(self, cipher, server.sock.sent[-1][0])[0]
                    records = wire_frame(packet)[1]
                    if drop_create and not dropped and any(
                        item.entity == mech.entity and item.op == world.OP_CREATE for item, _ in records
                    ):
                        dropped = True
                        continue
                    for item, fields in records:
                        if item.op == world.OP_CREATE:
                            known.add(item.entity)
                        if item.entity == pilot.entity and 44 in fields:
                            self.assertIn(pilot.body, known)
                            self.assertIn(mech.entity, known)
                            self.assertEqual(fields[44], {0: mech.entity, 1: mech.entity, 2: pilot.body})
                            bound = True
                    self.assertTrue(window.accept(packet.seq))
                    ack = Packet(peer.conn, KIND_CONTROL, sequence, window.expected, window.bits, ACK)
                    server._datagram(cipher.seal(ack), peer.address, 10.0)
                    if bound:
                        break
                self.assertTrue(bound)
                self.assertEqual(dropped, drop_create)

    def test_ready_control_release_and_rebind_publish_both_camera_handoffs(self):
        _run, pilot, _, _, mech = created.CreatedBodyTests().scenario()
        self.assertTrue(pilot.body_script.create_arrived)
        with patch.object(pilot.client, "queue_possession", wraps=pilot.client.queue_possession) as queue:
            body_entities.release_control(pilot)
            update, dependency = queue.call_args.args
            self.assertEqual(dependency, pilot.body)
            with patch.object(world, "update", wraps=world.update) as encode:
                update.build(0)
            self.assertEqual(encode.call_args.args[1][44], [pilot.body] * 3)
            body_entities.begin_initial_control(pilot)
            update, dependency = queue.call_args.args
            self.assertEqual(dependency, mech.entity)
            with patch.object(world, "update", wraps=world.update) as encode:
                update.build(0)
            self.assertEqual(encode.call_args.args[1][44], [mech.entity, mech.entity, pilot.body])

    def test_actual_return_replacement_and_hero_switch_publish_live_targets(self):
        for self_destruct in (False, True):
            with self.subTest(self_destruct=self_destruct):
                run, pilot, enemy, _, mech = created.CreatedBodyTests().scenario()
                fight = run.match.combat
                x, y, z = mech.position
                enemy.mover.position[:] = (x, y, z + (25 if self_destruct else 4))
                enemy.mover.body = None
                for _ in range(100):
                    run.step({})
                with patch.object(
                    pilot.client, "queue_possession", wraps=pilot.client.queue_possession,
                ) as queue:
                    if self_destruct:
                        component = mech.body_script.component
                        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                        run.step({pilot: (0, 0, 32)})
                    else:
                        fight.vitals[mech.entity] = Vitals(mech.entity, 5)
                        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.2, z))
                        run.step({enemy: (yaw, pitch, 1)})
                        self.assertTrue(mech.dead)
                    run.step({})
                    self.assertIsNone(pilot.controlled_mech)
                    self.assertEqual(queue.call_args.args[1], pilot.body)
                    self.assertEqual(run.match._possession(pilot)[44], [pilot.body] * 3)

                    for _ in range(225):
                        run.step({})
                    component = pilot.body_script.component
                    component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                    run.step({pilot: (0, 0, 32)})
                    for _ in range(150):
                        run.step({})
                        if pilot.controlled_mech is not None:
                            break
                    replacement = pilot.controlled_mech
                    self.assertIsNotNone(replacement)
                    self.assertIsNot(replacement, mech)
                    self.assertEqual(queue.call_args.args[1], replacement.entity)
                    self.assertEqual(run.match._possession(pilot)[44],
                                     [replacement.entity, replacement.entity, pilot.body])

                    self.assertTrue(run.match.switch_hero(pilot, find_hero("soldier")))
                    self.assertIsNone(pilot.controlled_mech)
                    self.assertNotIn(replacement, run.match.bots)
                    self.assertEqual(queue.call_args.args[1], pilot.body)
                    self.assertEqual(run.match._possession(pilot)[44], [pilot.body] * 3)
