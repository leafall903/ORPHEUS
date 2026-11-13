"""The separately created controlled mech needs the normal owner script codec."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

import test_dva_delivery_created_body as created
from test_game_packet_budget import connection, decoded
from test_game_repeated_lifecycles import wire_frame

from ow174.game import body_entities, dva_owner, observers
from ow174.game.content import find_hero
from ow174.game.link import ACK, KIND_CONTROL, Packet, ReceiveWindow
from ow174.game.script import decode
from ow174.game.script.driver import HUD_HEALTH, HUD_MAX_HEALTH, OWNER_HEALTH
from ow174.game.server import Client


class DvaOwnerProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    def scene(self):
        _, client, pilot, _ = connection()
        match = pilot.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.tick = 1000
        pilot.commands.last_frame = 1000
        self.assertTrue(match.switch_hero(pilot, find_hero("dva")))
        pilot.spawned = True
        client.queue_entities(match.spawn_updates(pilot))
        mech = pilot.controlled_mech
        client._ack_entity_create(mech.entity)
        delivery = client._bot_scripts[mech.entity]
        initial = delivery.stream.full_update
        plain = decode.read_chunk(initial.chunk.getvalue(), initial.chunk.count)
        known = {key: value.graph for key, value in plain.instances.items()}
        self.assertFalse(plain.owner)
        self.contract = json.loads((Path(__file__).resolve().parents[2]
                                    / "fixtures/behavior/dva-controlled-owner-stream-v1.json").read_text())
        return client, pilot, mech, delivery, initial, known

    def test_owner_frame_waits_for_baseline_ack_and_contains_actual_mech_hud(self):
        client, _, mech, delivery, initial, known = self.scene()
        stream = delivery.stream
        dva_owner.poll(client)
        self.assertEqual(stream.last, initial.chunk_last)
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        self.assertEqual(stream.cmfd, mech.body_script.frame)
        update = next(item for item in client.entities
                      if item.stream is stream and item.chunk_last == stream.last)
        frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
        self.assertTrue(frame.owner)
        self.assertFalse(frame.correction)
        self.assertEqual(frame.cmfd, mech.body_script.frame)
        weapon = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x050A)
        self.assertTrue(frame.instances[weapon.id].states[30][0])
        self.assertEqual((frame.owner, frame.correction), tuple(self.contract["expected"]["owner_flags"]))
        self.assertTrue(update.resend)
        pending = stream.last
        dva_owner.poll(client)
        self.assertEqual(stream.last, pending)  # no new frame over a lost/unacknowledged owner baseline
        stream.arrived(pending)
        dva_owner.poll(client)
        self.assertEqual(stream.last, pending)  # no repeated CmFD

    def test_owner_hud_numbers_follow_authoritative_mech_pools(self):
        client, pilot, mech, delivery, initial, known = self.scene()
        stream = delivery.stream
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        update = next(item for item in client.entities
                      if item.stream is stream and item.chunk_last == stream.last)
        frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
        self.assertEqual(pilot.match.combat.body_health(mech.entity), (600., 600.))
        self.assertEqual(frame.entity_vars.get(HUD_HEALTH), (600., ()))
        self.assertEqual(frame.entity_vars.get(HUD_MAX_HEALTH), (600., ()))
        stream.arrived(stream.last)
        vitals = pilot.match.combat.vitals[mech.entity]
        lost = vitals.damage(100.)
        mech.body_script.frame += 1
        dva_owner.poll(client)
        update = next(item for item in client.entities
                      if item.stream is stream and item.chunk_last == stream.last)
        frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, known)
        self.assertEqual(frame.entity_vars.get(HUD_HEALTH), (600. - lost, ()))
        self.assertEqual(stream.pending[stream.last][0].vars[(0, HUD_MAX_HEALTH)][0], 600.)
        self.assertNotIn(OWNER_HEALTH, frame.entity_vars)  # native51 still owns this derived value

    def test_missing_mech_health_does_not_fabricate_owner_hud_values(self):
        client, pilot, mech, delivery, initial, _ = self.scene()
        pilot.match.combat.vitals.pop(mech.entity)
        delivery.stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        snapshot = delivery.stream.pending[delivery.stream.last][0]
        self.assertNotIn((0, HUD_HEALTH), snapshot.vars)
        self.assertNotIn((0, HUD_MAX_HEALTH), snapshot.vars)

    def test_release_resets_plain_baseline_before_detached_observer_updates(self):
        client, pilot, mech, delivery, initial, _ = self.scene()
        stream = delivery.stream
        native_model = decode.ClientBody()
        native_model.apply(initial)
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        owner = next(item for item in client.entities
                     if item.stream is stream and item.chunk_last == stream.last)
        native_model.apply(owner)
        weapon = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x050A)
        self.assertTrue(native_model.states[(weapon.id, 30)])
        stream.arrived(stream.last)
        body_entities.release_control(pilot)
        dva_owner.poll(client)
        self.assertEqual(stream.cmfd, 0)
        update = next(item for item in client.entities
                      if item.stream is stream and item.chunk_last == stream.last)
        frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count)
        self.assertFalse(frame.owner)
        self.assertEqual(frame.first, 0)
        native_model.apply(update)
        self.assertFalse(native_model.states.get((weapon.id, 30), False))
        self.assertIn(mech.entity, client._bot_scripts)
        pending = stream.last
        dva_owner.poll(client)
        self.assertEqual(stream.last, pending)
        stream.arrived(pending)
        self.assertFalse(dva_owner.flush(client, delivery))

    def test_uncontrolled_owned_mech_keeps_observer_projection(self):
        client, pilot, _, delivery, initial, _ = self.scene()
        body_entities.release_control(pilot)
        delivery.stream.arrived(initial.chunk_last)
        self.assertFalse(dva_owner.flush(client, delivery))
        self.assertEqual(delivery.stream.cmfd, 0)

    def test_another_viewer_of_the_controlled_mech_stays_plain(self):
        client, pilot, mech, _, _, _ = self.scene()
        from test_game_packet_budget import KEY_IN, KEY_OUT, LinkCipher

        player = pilot.match.add_player(7, "Observer", find_hero("soldier"), 0, False)
        observer = Client(client.server, ("memory.test", 40007), 7,
                          LinkCipher(KEY_IN, KEY_OUT), player)
        player.client = observer
        observer.queue_entities([pilot.match._bot_create_update(mech)])
        observer._ack_entity_create(mech.entity)
        delivery = observer._bot_scripts[mech.entity]
        initial = delivery.stream.full_update
        delivery.stream.arrived(initial.chunk_last)
        dva_owner.poll(observer)
        self.assertEqual(delivery.stream.cmfd, 0)
        self.assertFalse(dva_owner.flush(observer, delivery))

    def test_held_destroy_can_drain_the_owner_to_plain_reset(self):
        client, pilot, mech, delivery, initial, _ = self.scene()
        stream = delivery.stream
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        owner_last = stream.last
        body_entities.release_control(pilot)
        client.queue_bot_destroy(mech.entity)
        self.assertIn(mech.entity, client._body_destroy_wait)
        stream.arrived(owner_last)
        dva_owner.poll(client)
        self.assertGreater(stream.last, owner_last)
        self.assertEqual(stream.cmfd, 0)
        reset = stream.full_update
        frame = decode.read_chunk(reset.chunk.getvalue(), reset.chunk.count)
        self.assertTrue(frame.full)
        self.assertFalse(frame.owner)
        stream.arrived(reset.chunk_last)
        self.assertFalse(dva_owner.flush(client, delivery))

    def test_release_uses_queued_terminal_snapshot_instead_of_the_old_live_body(self):
        client, pilot, mech, delivery, initial, _ = self.scene()
        stream = delivery.stream
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        stream.arrived(stream.last)
        live = mech.body_script.latest_live
        terminal = replace(live, vars=live.vars | {(0, OWNER_HEALTH): (0.0, ())})
        client.queue_bot_death(mech.entity, terminal)
        body_entities.release_control(pilot)
        dva_owner.poll(client)
        reset = stream.pending[stream.last][0]
        self.assertEqual(reset.vars[(0, OWNER_HEALTH)], (0.0, ()))
        self.assertEqual(stream.cmfd, 0)

    def test_parked_pilot_stays_plain_and_gets_owner_frames_only_after_return(self):
        client, pilot, _, _, _, _ = self.scene()
        script = pilot.body_script
        script.create_arrived = True  # acknowledged pilot create in this transport fixture
        pilot.match._body_frames(pilot, client)
        plain = script.stream.full_update
        frame = decode.read_chunk(plain.chunk.getvalue(), plain.chunk.count)
        self.assertFalse(frame.owner)
        expected = observers.snapshot(script)
        self.assertTrue(expected.states)
        self.assertEqual(script.stream.pending[plain.chunk_last][0].states, expected.states)
        native_model = decode.ClientBody()
        native_model.apply(plain)
        cosmetic = next((key for key, value in expected.states.items()
                         if value[0] == "STUStatescriptStateCosmeticEntity" and value[1]))
        self.assertTrue(native_model.states[cosmetic])
        script.stream.arrived(plain.chunk_last)
        last = script.stream.last
        pilot.match._body_frames(pilot, client)
        self.assertEqual(script.stream.cmfd, 0)
        self.assertEqual(script.stream.last, last)
        body_entities.release_control(pilot)
        pilot.match._body_frames(pilot, client)
        self.assertEqual(script.stream.cmfd, script.frame)
        self.assertGreater(script.stream.last, last)

    def test_reparking_an_owned_pilot_clears_its_owner_states(self):
        client, pilot, mech, _, _, _ = self.scene()
        script = pilot.body_script
        script.create_arrived = True
        body_entities.release_control(pilot)
        pilot.match._body_frames(pilot, client)
        script.stream.arrived(script.stream.last)
        pilot.match._body_frames(pilot, client)
        script.stream.arrived(script.stream.last)
        self.assertTrue(script.stream.base.states)
        pilot.controlled_mech = mech  # model an already-authorized handoff of the same VM
        pilot.match._body_frames(pilot, client)
        reset = script.stream.full_update
        self.assertFalse(decode.read_chunk(reset.chunk.getvalue(), reset.chunk.count).owner)
        self.assertEqual(script.stream.pending[reset.chunk_last][0].states,
                         observers.snapshot(script).states)
        self.assertEqual(script.stream.cmfd, 0)

    def test_parked_pilot_remote_state_changes_wait_for_ack_and_preserve_retry(self):
        client, pilot, _, _, _, _ = self.scene()
        script = pilot.body_script
        script.create_arrived = True
        pilot.match._body_frames(pilot, client)
        initial = script.stream.full_update
        native_model = decode.ClientBody()
        native_model.apply(initial)
        expected = observers.snapshot(script)
        key = next(key for key, value in expected.states.items()
                   if value[0] == "STUStatescriptStateCosmeticEntity" and value[1])
        instance = script.component.instances[key[0]]
        instance.states[key[1]].end(False)
        pilot.match._body_frames(pilot, client)
        self.assertEqual(script.stream.last, initial.chunk_last)
        script.stream.arrived(initial.chunk_last)
        pilot.match._body_frames(pilot, client)
        self.assertGreater(script.stream.last, initial.chunk_last)
        update = next(u for u in client.entities
                      if u.stream is script.stream and u.chunk_last == script.stream.last)
        self.assertTrue(update.resend)
        native_model.apply(update)
        self.assertFalse(native_model.states[key])
        last = script.stream.last
        pilot.match._body_frames(pilot, client)
        self.assertEqual(script.stream.last, last)

    def test_encrypted_owner_packet_loss_retries_exact_owner_chunk(self):
        client, pilot, mech, delivery, initial, known = self.scene()
        stream = delivery.stream
        stream.arrived(initial.chunk_last)
        dva_owner.poll(client)
        owner = next(item for item in client.entities
                     if item.stream is stream and item.chunk_last == stream.last)
        client.entities = [owner]
        client._own_controller_acked = True
        client._combat_notices[pilot.body] = None
        # The fixture connection's peer cipher is fresh: no frame has been sent.
        from test_game_packet_budget import KEY_IN, KEY_OUT, LinkCipher

        peer = LinkCipher(KEY_OUT, KEY_IN)
        window = ReceiveWindow()
        dropped = received = False
        for ordinal in range(1, 38):
            client.send_frame(1000 + ordinal, 10.0)
            packet = decoded(self, peer, client.server.sock.sent[-1][0])[0]
            chunks = [row.chunk for row, _ in wire_frame(packet)[1]
                      if row.entity == mech.entity and row.chunk is not None]
            if chunks and not dropped:
                dropped = True
                self.assertEqual(chunks[0].getvalue(), owner.chunk.getvalue())
                continue
            for chunk in chunks:
                frame = decode.read_chunk(chunk.getvalue(), chunk.count, known)
                self.assertTrue(frame.owner)
                self.assertEqual(chunk.getvalue(), owner.chunk.getvalue())
                received = True
            self.assertTrue(window.accept(packet.seq))
            ack = Packet(client.conn, KIND_CONTROL, ordinal, window.expected, window.bits, ACK)
            client.server._datagram(peer.seal(ack), client.address, 10.0)
            if received:
                break
        self.assertTrue(dropped)
        self.assertTrue(received)
        self.assertEqual(stream.base_last, owner.chunk_last)
