"""Synthetic source descriptors, snapshot projection, and unacknowledged source reversion."""
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import observers, statescript
from ow174.game.bits import BitReader, BitWriter
from ow174.game.script import decode, driver, runtime
from ow174.game.script.graph import Graph

FIXTURES = Path(os.environ.get('ORPHEUS_BEHAVIOR_FIXTURES') or
                Path(__file__).resolve().parents[2] / 'fixtures/behavior')
LAUNCHER = 0xA0000101


def definition():
    return Graph({'index': 0x1234, 'header': {'m_nodesBitCount': 0, 'm_statesBitCount': 0,
                  'm_remoteSyncNodesBitCount': 0, 'm_syncVarsBitCount': 0},
                  'fields': {}, 'items': 0, 'containers': {}, 'nodes': [], 'unknown_nodes': [],
                  'states': [], 'entries': [], 'remote': [], 'sync_vars': []})


class DescriptorSourceTests(unittest.TestCase):
    def test_fixed_codec_vectors_and_legacy_default(self):
        fixture = json.loads((FIXTURES / 'statescript-descriptor-source-v1.json').read_text())
        graph = definition()
        for case in fixture['input']['cases']:
            for make, write in ((lambda **kw: statescript.Instance(1, graph, **kw), statescript._descriptor),
                                (lambda **kw: statescript.InstanceWire(1, graph, **kw),
                                 statescript._descriptor_of)):
                with self.subTest(case=case, writer=write.__name__):
                    out = BitWriter()
                    write(out, make(source_entity=case['source']))
                    self.assertEqual((out.count, out.getvalue().hex()), (case['bits'], case['hex']))
                    reader, decoded = BitReader(out.getvalue()), decode.DecodedInstance(1)
                    decode._descriptor(reader, decoded)
                    self.assertEqual(decoded.source_entity, case['source'])
                    self.assertEqual(reader.pos, out.count)
                    if case['source'] is None:
                        legacy = BitWriter()
                        legacy.bits(graph.index, 16)
                        legacy.bits(0, 4)
                        self.assertEqual((out.count, out.getvalue()), (legacy.count, legacy.getvalue()))
        for invalid in fixture['input']['invalid']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                statescript._descriptor_of(
                    BitWriter(), statescript.InstanceWire(1, graph, source_entity=invalid),
                )

    def test_runtime_opt_in_and_snapshot_projection(self):
        graph = definition()
        component = runtime.Component(0xA0080000, world=runtime.World(), strict=True)
        instance = component.create(graph, init=False)
        instance.set_source_entity(LAUNCHER)
        self.assertIsNone(instance.descriptor_source_entity)
        instance.set_source_entity(LAUNCHER, publish_source=True)
        self.assertEqual(instance.descriptor_source_entity, LAUNCHER)
        script = SimpleNamespace(component=component, callout_snapshot=lambda: driver.Snapshot(10),
                                 replicated_instances=lambda: [instance], replicated_vars=lambda *a: [],
                                 _add_var=driver.BodyScript._add_var, _add_state=driver.BodyScript._add_state)
        self.assertEqual(driver.BodyScript.snapshot(script).sources, {instance.id: LAUNCHER})
        with patch.object(observers, "_remote_snapshot_states", return_value=()):
            self.assertEqual(observers.snapshot(script).sources, {instance.id: LAUNCHER})
        instance.set_source_entity(0)
        self.assertEqual(instance.descriptor_source_entity, 0)
        with self.assertRaises(ValueError):
            instance.set_source_entity(-1, publish_source=True)
        self.assertEqual(instance.descriptor_source_entity, 0)

    def test_full_delta_and_unacknowledged_reversion(self):
        graph = definition()
        snap = driver.Snapshot(10, {1: (graph.index, None)}, sources={1: LAUNCHER})
        script = SimpleNamespace(snapshot=lambda: snap, graphs=lambda: {1: graph}, frame_ms=16,
                                 stream=SimpleNamespace(last=2))
        with patch.object(decode, 'graph_of', return_value=graph):
            tree, base = driver.BodyScript.tree_frame(script)
            self.assertEqual(base.sources, snap.sources)
            for payload in (tree, observers.full_frame(script, snap)):
                model = decode.ClientBody()
                chunk = statescript.chunk(payload, 0, 1)
                model.apply(SimpleNamespace(chunk=chunk))
                self.assertEqual(model.sources, {1: LAUNCHER})
            for initial in (driver.Snapshot(), driver.Snapshot(9, dict(snap.instances), sources={1: 0})):
                payload, keys, projected = driver.BodyScript.delta(script, initial, set(), 10, False)
                chunk = statescript.chunk(payload, 2, 1)
                frame = decode.read_chunk(chunk.getvalue(), chunk.count, {1: graph.index})
                self.assertEqual(frame.instances[1].source_entity, LAUNCHER)
                self.assertIn(('source', 1), keys)
                self.assertEqual(projected.sources, {1: LAUNCHER})
            # B may have arrived despite its lost ACK; resend A even when A equals the ACK baseline.
            for owner in (False, True):
                touched = {('source', 1), ('inst', 1)}
                if owner:
                    payload, keys, _ = driver.BodyScript.delta(script, base, touched, 11, False)
                else:
                    payload, keys = observers._delta_from({1: graph}, base, snap, base.instances, touched)
                chunk = statescript.chunk(payload, 3, 1)
                frame = decode.read_chunk(chunk.getvalue(), chunk.count, {1: graph.index})
                self.assertEqual(frame.instances[1].source_entity, LAUNCHER)
                self.assertIn(('source', 1), keys)
            # Delayed observer full uses captured metadata, not the current VM's source.
            old = driver.Snapshot(9, dict(snap.instances), sources={1: 0})
            payload = observers.full_frame(script, old)
            chunk = statescript.chunk(payload, 0, 1)
            frame = decode.read_chunk(chunk.getvalue(), chunk.count)
            self.assertEqual(frame.instances[1].source_entity, 0)


if __name__ == '__main__':
    unittest.main()
