"""Actual Shadow confirm -> healthless actor create ACK -> effect reference -> cleanup."""

import inspect
import struct
import unittest

from ow174.game import reaper_shadow_actor as actors
from ow174.game import world
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.body_vm_snapshot import snapshot_digest as body_digest
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import expr
from ow174.game.server_snapshot import export_server_state
from ow174.game.server_snapshot import snapshot_digest as server_digest
from tests.runtime import test_game_reaper as scenarios
from tests.runtime import test_game_repeated_lifecycles as wire_tests
from tests.runtime import test_server_journal as journal
from tests.runtime.test_script_driver import ClientModel


class ReaperShadowActorTests(unittest.TestCase):
    def test_actual_confirm_received_create_loss_late_bootstrap_and_cleanup(self):
        # Retain definition/position from the independent actual wire reader.
        reader_source = inspect.getsource(wire_tests.entity_record)
        reader_source = reader_source.replace(
            "    if op == OP_CREATE:",
            "    definition = None\n    position = None\n    if op == OP_CREATE:",
            1,
        )
        reader_source = reader_source.replace(
            "            reader.bits(12 if reader.bit() else 20 if reader.bit() else 32)",
            "            definition = reader.bits(12 if reader.bit() else 20 if reader.bit() else 32)",
        )
        reader_source = reader_source.replace(
            "                reader.bits(7 * 32)",
            "                position = tuple(struct.unpack('<f', struct.pack('<I', reader.bits(32)))[0] "
            "for _ in range(3))\n                reader.bits(4 * 32)",
        )
        reader_source = reader_source.replace(
            "    return result",
            "    result['definitionIndex'] = definition\n"
            "    result['position'] = position\n    return result",
        )
        scope = {**vars(wire_tests), "struct": struct}
        exec(reader_source, scope)
        exec(inspect.getsource(wire_tests.wire_frame), scope)
        wire_frame = scope["wire_frame"]
        helper = journal.ServerJournalTests()
        session, sent = helper.make()
        peers, players = helper.connected(session, sent, first_hero=journal.REAPER)
        owner, observer = players
        script = owner.body_script
        shadow = next(i for i in script.component.instances.values() if i.graph.index == 0xF2)
        state = shadow.state(43)
        self.assertIs(type(state), actors.ShadowActor)
        self.assertEqual((state.node.pos, state.index, state.node.remote), (74, 43, -1))
        self.assertEqual(state.actor_entity, 0)
        before_body = body_digest(export_body_vm(script))
        models = [ClientModel(journal.REAPER) for _ in peers]
        known = [set(), set()]
        cursor, now, dropped = 0, 100.928, False
        events = [[], []]

        def receive(drop_create=False):
            nonlocal cursor, dropped
            for raw, _ in sent[cursor:]:
                for k, peer in enumerate(peers):
                    if peek_connection(raw) != peer.handoff.conn:
                        continue
                    packet = peer.cipher.open(raw)
                    self.assertIsNotNone(packet)
                    if packet.kind != KIND_FRAME:
                        continue
                    _, records = wire_frame(packet)
                    if (
                        drop_create
                        and k == 1
                        and not dropped
                        and state.actor_entity
                        and any(
                            u.entity == state.actor_entity and u.op == world.OP_CREATE for u, _ in records
                        )
                    ):
                        # Lose the actual datagram and its ACK, not just a model update.
                        dropped = True
                        peer.cursor = len(sent)
                        continue
                    for update, fields in records:
                        if update.op == world.OP_CREATE:
                            known[k].add(update.entity)
                            if update.entity == state.actor_entity:
                                self.assertEqual(fields["definitionIndex"], 0x1F0E)
                                self.assertFalse({k: v for k, v in fields.items() if type(k) is int})
                                for actual, point in zip(fields["position"], state.actor_point, strict=True):
                                    self.assertAlmostEqual(actual, point, places=5)
                                events[k].append("actor_create_received")
                        elif update.op == world.OP_DESTROY:
                            known[k].discard(update.entity)
                            events[k].append(("destroy", update.entity))
                        if update.entity == owner.body and update.chunk is not None:
                            models[k].apply(update)
                            value = models[k].vars.get((shadow.id, 12105))
                            if state.actor_entity and value == expr.Entity(state.actor_entity):
                                self.assertIn(state.actor_entity, known[k])
                                events[k].append("actor_reference_received")
                    peer.window.accept(packet.seq)
            cursor = len(sent)
            for peer in peers:
                peer.cursor = len(sent)

        def step(buttons=0, drop_create=False):
            nonlocal now
            now += 0.016
            peers[0].commands(script.frame + 1, 1, buttons, pitch=5000, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            session.tick(now)
            receive(drop_create)
            for peer in peers:
                peer.send(now=now)

        receive()
        for _ in range(24):
            step()
        step(16)
        for _ in range(8):
            step()
        self.assertEqual(state.actor_entity, 0)
        step(1, drop_create=True)
        actor = state.actor_entity
        self.assertNotEqual(actor, 0)
        self.assertNotEqual(actor, owner.body)
        self.assertEqual(shadow.vars[12105].value(), expr.Entity(actor))
        self.assertIs(actors.lookup(owner.match, actor), state)
        self.assertEqual(owner.match.script_body_position(actor, owner.body), expr.Vec3(*state.actor_point))
        self.assertNotIn(actor, owner.match.combat.vitals)
        self.assertFalse(any(t.entity == actor for t in owner.match.combat._targets()))
        for _ in range(12):
            step(drop_create=not dropped)
        self.assertTrue(dropped)
        for k in (0, 1):
            self.assertEqual(models[k].vars.get((shadow.id, 12105)), expr.Entity(actor))
            self.assertIn("actor_create_received", events[k])
            self.assertIn("actor_reference_received", events[k])
        self.assertNotEqual(before_body, body_digest(export_body_vm(script)))

        def capture_server():
            return server_digest(
                export_server_state(
                    session.server,
                    archives=session._matches.values(),
                    collision_provider=session._collision_reads,
                    assets=session.registry,
                )
            )

        stored_body = body_digest(export_body_vm(script))
        stored_server = capture_server()
        original_point = state.actor_point
        state.actor_point = (original_point[0] + 0.125, *original_point[1:])
        self.assertNotEqual(stored_body, body_digest(export_body_vm(script)))
        self.assertNotEqual(stored_server, capture_server())
        state.actor_point = original_point
        self.assertEqual(stored_body, body_digest(export_body_vm(script)))
        # Existing late receiver path: recreate this actor and remote body, then
        # wait for the actual create generation ACK before the current H0 body.
        models[1] = ClientModel(journal.REAPER)
        known[1].discard(actor)
        observer.client.queue_entities(
            [*actors.spawn_updates(owner.match), owner.match.remote_body_create(observer, owner)]
        )
        for _ in range(12):
            step()
        self.assertEqual(models[1].vars.get((shadow.id, 12105)), expr.Entity(actor))
        for _ in range(180):
            step()
        self.assertEqual(state.actor_entity, 0)
        self.assertIsNone(actors.lookup(owner.match, actor))
        self.assertIsNone(owner.match.script_body_position(actor, owner.body))
        self.assertEqual(shadow.vars[12105].value(), expr.Entity(actor))  # native cleanup retains output
        for k in (0, 1):
            self.assertNotIn(actor, known[k])
            self.assertIn(("destroy", actor), events[k])
        body_digest(export_body_vm(script))

    def test_actual_confirm_source_retirement_destroys_only_cached_actor(self):
        run, owner, _enemy, _target = scenarios.ReaperTests().scenario()
        shadow = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0xF2)
        state = shadow.state(43)
        run.step({owner: (0, 5000, 16)})
        for _ in range(8):
            run.step({owner: (0, 5000, 0)})
        run.step({owner: (0, 5000, 1)})
        entity = state.actor_entity
        self.assertNotEqual(entity, 0)
        script = owner.body_script
        script.retire()
        self.assertEqual(state.actor_entity, 0)
        self.assertIsNone(actors.lookup(owner.match, entity))
        self.assertIsNone(owner.match.script_body_position(entity, owner.body))
        self.assertEqual(shadow.vars[12105].value(), expr.Entity(entity))
        state.on_cleanup()  # idempotent: an old source cannot retire another actor
