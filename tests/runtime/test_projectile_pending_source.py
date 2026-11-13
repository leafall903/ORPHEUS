"""Encrypted delivery through a pending source baseline, without a native client."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_repeated_lifecycles as wire
import test_server_journal as journal

from ow174.game import world
from ow174.game.bits import BitReader
from ow174.game.script.decode import ClientBody

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures/behavior/projectile-pending-source-v1.json").read_text()
)


class PendingSourceTests(unittest.TestCase):
    def run_case(self, name):
        case = next(row for row in CONTRACT["cases"] if row["name"] == name)
        inputs, expected = CONTRACT["input"], CONTRACT["expected"]
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=int(inputs["hero"], 16))
        owner, observer = players

        def replace_body():
            # The synthetic account has no persisted skin catalogue.
            with patch.object(owner.match, "skin_of", lambda account, hero: owner.skin):
                self.assertTrue(owner.match.switch_hero(owner, owner.hero, preserve_pose=True))

        replace_body()
        source_body = owner.body
        fight = owner.match.combat
        observer.mover.position = list(inputs["target_position"])
        observer.mover.body = None
        model = ClientBody.for_hero(owner.hero.guid)
        shot = None
        shots, events, volleys = [], [], []
        definitions = {}
        identity = [None]
        native_update, native_record, native_shot = wire.EntityUpdate, wire.entity_record, wire.read_shot

        def update(entity):
            identity[0] = entity
            return native_update(entity)

        def record(reader, op):
            if op == world.OP_CREATE:
                peek = BitReader(reader.data, reader.pos)
                peek.bits(14)
                if not peek.bit():
                    definitions[identity[0]] = peek.bits(12 if peek.bit() else 20 if peek.bit() else 32)
            return native_record(reader, op)

        def read_shot(reader):
            value = native_shot(reader)
            shots.append(value)
            return value

        now = inputs["start_time"]
        for offset in range(inputs["ticks"]):
            now += inputs["tick_seconds"]
            if offset == case["replace_offset"]:
                replace_body()
                self.assertNotEqual(source_body, owner.body)
            buttons = inputs["buttons"] if offset == inputs["fire_offset"] else 0
            peers[0].commands(owner.body_script.frame + 1, 1, buttons, now=now)
            peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
            cursor = len(sent)
            session.tick(now)
            if fight.rockets and shot is None:
                shot = fight.rockets[0].shot
            if offset >= case["resume_offset"]:
                for data, address in sent[cursor:]:
                    if address != peers[1].address:
                        continue
                    packet = peers[1].cipher.open(data)
                    self.assertIsNotNone(packet)
                    if packet.kind != journal.KIND_FRAME:
                        continue
                    with (
                        patch.object(wire, "EntityUpdate", update),
                        patch.object(wire, "entity_record", record),
                        patch.object(wire, "read_shot", read_shot),
                    ):
                        _, records = wire.wire_frame(packet)
                    for item, _ in records:
                        if (definitions.get(item.entity) == expected["projectile_definition"]
                                and item.op in (world.OP_CREATE, world.OP_DESTROY)):
                            events.append((item.entity, item.op))
                        if item.entity == source_body and item.chunk is not None:
                            frame = model.apply(item)
                            for instance_id, instance in frame.instances.items():
                                graph = instance.graph or model.instances.get(instance_id)
                                if graph == expected["source_graph"] and 0 in instance.states:
                                    _, payload = instance.states[0]
                                    volleys.append(
                                        (instance_id, payload.get("counter"), payload.get("volleys"))
                                    )
            peers[0].drain()
            peers[0].send(now=now)
            peers[1].drain(lose=offset < case["resume_offset"])
            peers[1].send(now=now)
            if offset == expected["pending_at_offset"] and name != "normal":
                self.assertIsNotNone(shot)
                pending = [d for d in shot.observers if d.viewer is observer]
                self.assertEqual(len(pending), 1, "registered observer was omitted at fire time")
                delivery = pending[0]
                self.assertIsNotNone(delivery.pending_create)
                self.assertFalse(delivery.creation.received)
                self.assertFalse(delivery.launch_arrival.received)
            if offset == expected["late_baseline_live_at_offset"] and name == "late":
                watch = fight.observers.watches[(observer.slot, source_body)]
                self.assertIsNotNone(watch.stream.base)
                self.assertTrue(any(rocket.shot is shot for rocket in fight.rockets))
            if offset == case["resume_offset"] - 1 and name == "expired":
                self.assertGreaterEqual(session.server.tick, delivery.deadline)
                self.assertNotIn(delivery, fight.projectiles.observed)
                self.assertIsNone(delivery.stream.base)

        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition & 0xFFFFFFFF, expected["projectile_definition"])
        deliveries = [d for d in shot.observers if d.viewer is observer]
        self.assertEqual(len(deliveries), 1)
        self.assertTrue(all(d not in fight.projectiles.observed for d in shot.observers))
        self.assertEqual(len(shots), case["expected_shots"])
        if shots:
            launched = shots[0]
            self.assertEqual(launched["ids"], list(deliveries[0].record.ids))
            self.assertEqual(launched["state"], expected["volley_state"])
            self.assertEqual(launched["activation"], expected["activation"])
            self.assertEqual(launched["volleys"], expected["volleys"])
            self.assertIn((launched["instance"], expected["activation"], expected["volleys"]), volleys)
            self.assertEqual([op for _, op in events], expected["actor_operations_for_delivered_shot"])
            self.assertEqual({entity for entity, _ in events}, {deliveries[0].record.ids[0]})
            self.assertTrue(deliveries[0].creation.received)
            self.assertTrue(deliveries[0].launch_arrival.received)
        else:
            self.assertEqual(events, [])
            self.assertFalse(deliveries[0].creation.received)
            self.assertFalse(deliveries[0].launch_queued)

    def test_delayed_source_baseline(self):
        self.run_case("late")

    def test_normal_source_baseline(self):
        self.run_case("normal")

    def test_pending_expiry(self):
        self.run_case("expired")

    def test_pending_source_body_replacement(self):
        self.run_case("replaced")
