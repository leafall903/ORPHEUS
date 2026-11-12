"""Public behavior contracts consumed through normal server entry points."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import statescript
from ow174.game.script import decode, nodes, runtime
from ow174.game.script.driver import BodyStream, Snapshot, WheelCallout, frame_callout
from ow174.game.script.expr import Entity, Vec3
from ow174.game.script.graph import Graph
from ow174.jam.cipher import Jam
from ow174.jam.framing import FrameReader, send_frame
from tests.script_fixtures import graph, node, plug, setter

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class WireSink:
    def __init__(self):
        self.data = bytearray()

    def sendall(self, data):
        self.data.extend(data)


class JamFramingContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((FIXTURES / "jam-framing-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["case_id"], "jam-framing.synthetic-stream.v1")
        self.assertEqual(self.contract["target_build"], 104319)
        self.assertEqual(self.contract["evidence"], "synthetic-reference")
        self.key = bytes.fromhex(self.contract["input"]["key_hex"])
        self.expected_wire = bytes.fromhex(self.contract["expected"]["wire_hex"])
        self.expected_payloads = [
            bytes.fromhex(payload) for payload in self.contract["expected"]["payloads_hex"]
        ]

    def test_sender_matches_shared_wire_bytes(self):
        sink = WireSink()
        cipher = Jam(self.key)
        for payload in self.contract["input"]["payloads_hex"]:
            send_frame(sink, cipher, bytes.fromhex(payload))
        self.assertEqual(bytes(sink.data), self.expected_wire)

    def test_receiver_matches_shared_payloads_at_every_split(self):
        for split in range(len(self.expected_wire) + 1):
            with self.subTest(split=split):
                reader = FrameReader(Jam(self.key))
                payloads = list(reader.feed(self.expected_wire[:split]))
                payloads.extend(reader.feed(self.expected_wire[split:]))
                self.assertEqual(payloads, self.expected_payloads)

        reader = FrameReader(Jam(self.key))
        payloads = []
        for byte in self.expected_wire:
            payloads.extend(reader.feed(bytes([byte])))
        self.assertEqual(payloads, self.expected_payloads)


class SeismicSlamWaveContractTests(unittest.TestCase):
    def test_child_reads_new_targets_before_immediate_wave_finishes(self):
        contract = json.loads((FIXTURES / "seismic-slam-wave-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["case_id"], "seismic-slam.immediate-wave-lifecycle.v1")
        self.assertEqual(contract["target_build"], 104319)
        self.assertEqual(contract["evidence"], "synthetic-reference")
        inputs, expected = contract["input"], contract["expected"]
        targets = (runtime.INSTANCE, 375)

        class SamplingWorld(runtime.World):
            def __init__(self):
                self.events = []
                self.samples = iter((inputs["begin_candidates"], inputs["tick_candidates"]))

            def shockwave_targets(self, state, reach):
                self.events.append(["sample", reach])
                return tuple(next(self.samples))

            def modify_health(self, state, elapsed_ms):
                current = state.instance.context().var(*targets)
                self.events.append(["read_targets", [entity.id for entity in current]])

        definition = graph([
            node(95, "STUStatescriptStateShockwave", state=0,
                 m_3418CEA4={"$": "STUConfigVarBool", "m_value": 1},
                 m_6A79BD98={"$": "STUConfigVarFloat", "m_value": inputs["reach"]},
                 m_8A2905A0={"$": "STUConfigVarDynamic", "m_identifier": 375},
                 m_subgraphPlug={"$": "STU_904BFCEC", "m_FF3DAF1E": [1],
                                 "links": [[93, "m_beginPlug"]]},
                 m_onFinishedPlug=plug((94, "m_inPlug"))),
            node(93, "STUStatescriptStateModifyHealth", state=1),
            setter(94, 99),
        ])
        definition.index = 0x0B27
        world = SamplingWorld()
        component = runtime.Component(
            inputs["owner"], frame_ms=inputs["frame_ms"], world=world, seed=inputs["seed"], strict=True,
        )
        component.now = inputs["begin_time_ms"]
        instance = component.create(definition)
        instance.write(targets, inputs["initial_targets"])
        wave, child = instance.state(0), instance.state(1)
        self.assertIsInstance(wave, nodes.Shockwave)
        wave.request(runtime.BEGIN)
        component.run_now()
        self.assertEqual((wave.active, child.active), (expected["active_after_begin"],) * 2)
        self.assertEqual(wave.payload(), {"has_list": expected["auxiliary_list_present"]})

        component.run_frame(inputs["first_tick_frame"])
        self.assertEqual((wave.active, child.active), (expected["active_after_tick"],) * 2)
        self.assertEqual(instance.vars[7].value(), expected["finish_marker"])
        self.assertEqual(world.events, expected["events"])


class SeismicSlamPlacementContractTests(unittest.TestCase):
    def test_confirmation_publishes_the_static_destination_before_travel(self):
        contract = json.loads((FIXTURES / "seismic-slam-placement-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["case_id"], "seismic-slam.airborne-placement.v1")
        self.assertEqual(contract["target_build"], 104319)
        self.assertEqual(contract["evidence"], "synthetic-reference")
        inputs, expected = contract["input"], contract["expected"]

        def variable(identifier, scope=runtime.INSTANCE):
            return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": scope}

        class LandingWorld(runtime.World):
            def __init__(self):
                self.point = None
                self.query_frames = []

            def select_landing(self, state):
                self.query_frames.append(state.instance.component.frame)
                return self.point

        definition = graph([
            node(15, "STU_4C96470A", state=0,
                 m_A6BB0869={"$": "STUConfigVarBool", "m_value": 1},
                 m_B02B1FD7=variable(1321), m_C3240FFC=variable(1627),
                 m_30710B76=variable(431), m_1549F0F9=variable(1883),
                 m_transitionPlug=[plug((155, "m_inPlug"))],
                 m_F6955E81=plug((15, "m_F198FD3A"), (70, "m_inPlug"))),
            node(155, "STU_3387AB5D",
                 m_condition={"$": "STU_919FD47C", "m_expression": {
                     "m_opcodes": [8, 0, 2, 4, 8, 1, 0], "m_dynamicVars": [variable(1627), variable(1321)],
                 }},
                 m_truePlug=plug((76, "m_beginPlug"))),
            node(76, "STUStatescriptStateBooleanSwitch", state=1,
                 m_condition=variable(10473, runtime.ENTITY),
                 m_onBeginPlug=plug((164, "m_inPlug")),
                 m_trueSubgraphPlug={"$": "STU_904BFCEC", "m_FF3DAF1E": [2], "links": [[18, "m_beginPlug"]]}),
            node(164, "STUStatescriptActionSetVar", m_out_Var=variable(432), m_value=variable(431)),
            node(70, "STUStatescriptActionSetVar", m_out_Var=variable(11174),
                 m_value={"$": "STUConfigVarBool", "m_value": 1}),
            node(18, "STUStatescriptStateMoveTo", state=2,
                 m_B060F688=0, m_865F3D01=variable(431), m_destinationEntity=variable(1883),
                 m_speed={"$": "STUConfigVarFloat", "m_value": inputs["speed"]}),
        ])
        definition.index = 0x0B27
        world = LandingWorld()
        component = runtime.Component(
            inputs["owner"], frame_ms=inputs["frame_ms"], world=world, seed=inputs["seed"], strict=True,
        )
        component.set(10473, True)
        instance = component.create(definition)
        instance.write((runtime.INSTANCE, 431), Vec3(*inputs["old_destination"]))
        instance.write((runtime.INSTANCE, 1883), Entity(inputs["old_entity"]))
        instance.write((runtime.INSTANCE, 11174), False)
        selector, move_to = instance.state(0), instance.state(2)
        self.assertIsInstance(selector, nodes.PlacementSelector)
        self.assertIsInstance(move_to, nodes.MoveTo)
        selector.request(runtime.BEGIN)
        component.run_now()

        for tick, result in zip(inputs["ticks"], expected["ticks"], strict=True):
            with self.subTest(frame=tick["frame"]):
                instance.write((runtime.INSTANCE, 1321), tick["confirm"])
                world.point = Vec3(*tick["point"]) if tick["point"] is not None else None
                component.run_frame(tick["frame"])

                self.assertIs(selector.active, result["selector_active"])
                self.assertIs(instance.context().var(runtime.INSTANCE, 1627), result["valid"])
                self.assertEqual(list(instance.vars[431].value()), result["destination"])
                self.assertEqual(instance.vars[1883].value(), Entity(result["entity"]))
                movement = move_to.move_request()
                self.assertEqual([list(movement[0]), movement[1]] if movement is not None else None,
                                 result["move_to"])

        self.assertEqual(world.query_frames, expected["query_frames"])
        self.assertEqual(list(instance.vars[432].value()), expected["confirmed_preview"])
        self.assertIs(instance.vars[11174].value(), expected["manual_confirm_plug_followed"])


class ShortFrameActionContractTests(unittest.TestCase):
    def test_owner_and_observer_keep_the_first_stamp_until_ack(self):
        contract = json.loads((FIXTURES / "body-short-action-retry-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["case_id"], "statescript.short-frame-action-retry.v1")
        self.assertEqual(contract["target_build"], 104319)
        self.assertEqual(contract["evidence"], "synthetic-reference")
        inputs, expected = contract["input"], contract["expected"]
        instance, action = inputs["instance"], inputs["action"]
        instance_id, graph_index = instance["id"], instance["graph_index"]
        action_node = node(action["position"], "SyntheticAction")
        action_node.update(rid=action["raw_index"], remote=action["remote_index"])
        definition = Graph({
            "index": graph_index,
            "header": {
                "m_nodesBitCount": action["raw_bits"], "m_remoteSyncNodesBitCount": action["remote_bits"],
                "m_statesBitCount": 0, "m_syncVarsBitCount": 0,
            },
            "fields": {}, "items": 1, "containers": {}, "nodes": [action_node], "unknown_nodes": [],
            "states": [], "entries": [], "remote": [None] * action["remote_index"] + [action["position"]],
            "sync_vars": [],
        })
        definitions = {instance_id: definition}
        instances = {instance_id: (graph_index, None)}
        cmfd = inputs["command_frame"]
        baseline = Snapshot(cmfd, instances)
        event = WheelCallout(
            action["serial"], instance_id, instance["start_time_ms"], action["position"], action["time_ms"],
            None, (),
        )
        pending = Snapshot(cmfd, instances, callout=event.serial, callouts=(event,))

        with patch.object(decode, "graph_of", side_effect={graph_index: definition}.__getitem__):
            for mode in ("owner", "observer"):
                with self.subTest(mode=mode):
                    stream = BodyStream(inputs["entity"])
                    client = decode.ClientBody()
                    tree = statescript.body_full_frame([statescript.InstanceWire(instance_id, definition)],
                                                       {}, definitions)
                    initial = stream.full(tree, baseline)
                    self.assertEqual(client.apply(initial).actions, [])
                    stream.arrived(initial.chunk_last)
                    for outcome, number, wire in zip(inputs["delivery"], expected["chunk_last"],
                                                     expected[mode]["chunk_hex"], strict=True):
                        keys = set()
                        queued = frame_callout(stream.base, pending, stream.touched(), keys, stream.last + 1)
                        if mode == "owner":
                            delta = statescript.owner_delta(cmfd, False, [], {}, definitions, callout=queued)
                        else:
                            delta = statescript.remote_delta([], {}, definitions, callout=queued)
                        update = stream.delta(delta, pending, keys, cmfd if mode == "owner" else 0)
                        self.assertEqual(update.chunk_last, number)
                        self.assertEqual(update.chunk.count, expected[mode]["bit_count"])
                        self.assertEqual(update.chunk.getvalue().hex(), wire)
                        received = client.apply(update)
                        self.assertEqual(received.owner, mode == "owner")
                        self.assertEqual([list(item) for item in received.actions], [expected["action"]])
                        if outcome == "lost":
                            stream.lost(number)
                        else:
                            self.assertEqual(outcome, "acknowledged")
                            stream.arrived(number)
                    self.assertEqual(stream.base.callout, expected["acknowledged_serial"])
                    self.assertIsNone(frame_callout(stream.base, pending, stream.touched(), set(),
                                                    stream.last + 1))


if __name__ == "__main__":
    unittest.main()
