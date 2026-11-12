"""Public behavior contracts consumed through normal server entry points."""

import json
import os
import unittest
from pathlib import Path

from ow174.game.script import nodes, runtime
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


if __name__ == "__main__":
    unittest.main()
