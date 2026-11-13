"""Exact authoritative class storage; strict/base roles and codec errors stay strict."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import statescript
from ow174.game.bits import BitWriter
from ow174.game.script import graph, placement_byte, runtime
from ow174.game.script.driver import BodyWorld

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                       / 'placement-byte-authority-v1.json').read_text())


class PlacementByteAuthorityTests(unittest.TestCase):
    def state(self, cls):
        # A synthetic storage/role contract. Actual BodyScript command and wire
        # dispatch is exercised separately by the four heroes and Doom Uppercut.
        world = BodyWorld.__new__(BodyWorld)
        world.body = 7
        component = runtime.Component(7, world=world)
        typed = {'$': CONTRACT['input']['typed_class']}
        return SimpleNamespace(
            instance=SimpleNamespace(component=component),
            node=SimpleNamespace(cls=cls, field=lambda _: typed),
            active=False, ending=True,
        )

    def test_exact_authority_lifecycle_and_excluded_roles(self):
        for cls in CONTRACT['input']['classes']:
            with self.subTest(cls=cls):
                state = self.state(cls)
                placement_byte.begin(state)
                self.assertEqual(state.placement_byte, CONTRACT['expected']['constructor_byte'])
                for previous in CONTRACT['input']['initial_bytes']:
                    state.placement_byte = previous
                    placement_byte.begin(state)

                    self.assertEqual(state.placement_byte, CONTRACT['expected']['begin_byte'])
                    placement_byte.typed_input(state, CONTRACT['input']['typed_path'])
                    self.assertEqual(state.placement_byte, CONTRACT['expected']['typed_byte'])
                placement_byte.begin(state)
                with patch.object(state.node, 'field', return_value={'$': 'wrong-type'}):
                    placement_byte.typed_input(state, CONTRACT['input']['typed_path'])
                placement_byte.typed_input(state, 'wrong-path')
                self.assertEqual(state.placement_byte, 0)
                component = state.instance.component
                for host, name, value in ((component, 'strict', True),
                                          (component, 'world', runtime.World()),
                                          (component, 'entity', 8),
                                          (state.node, 'cls', 'STUStatescriptState')):
                    with self.subTest(role=name), patch.object(host, name, value):
                        del state.placement_byte
                        placement_byte.begin(state)
                        placement_byte.typed_input(state, CONTRACT['input']['typed_path'])
                        self.assertFalse(hasattr(state, 'placement_byte'))
                        self.assertIsNone(placement_byte.payload(state))
                    placement_byte.begin(state)

        # Also construct both registered state classes through the actual VM
        # factory in the excluded roles; a matching STU hash is insufficient.
        body_world = BodyWorld.__new__(BodyWorld)
        body_world.body = 7
        for strict, world in ((True, body_world), (False, runtime.World())):
            for position in (69, 72):
                component = runtime.Component(7, world=world, strict=strict)
                instance = component.create(graph.graph(0x1571), init=False)
                if strict and position == 69:
                    # The existing strict registry selects SummonerPlacement,
                    # which rejects this graph. Do not replace or relax it.
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        instance.node_state(instance.graph.node(position))
                    continue
                state = instance.node_state(instance.graph.node(position))
                self.assertFalse(hasattr(state, 'placement_byte'))
                self.assertEqual(state.payload(), {})
                placement_byte.typed_input(state, CONTRACT['input']['typed_path'])
                self.assertFalse(hasattr(state, 'placement_byte'))
        component = runtime.Component(7, world=body_world, strict=True)
        instance = component.create(graph.graph(0x156B), init=False)
        state = instance.node_state(instance.graph.node(57))
        self.assertEqual(type(state).__name__, 'SummonerPlacement')
        self.assertFalse(hasattr(state, 'placement_byte'))
        self.assertEqual(state.payload(), {'m_B4802CE3': 0})

    def test_invalid_explicit_and_missing_storage_do_not_acquire_defaults(self):
        for cls in CONTRACT['input']['classes']:
            state = self.state(cls)
            for value in CONTRACT['input']['invalid_explicit']:
                state.placement_byte = value
                payload = placement_byte.payload(state)
                self.assertIs(payload['m_B4802CE3'], value)
                with self.subTest(cls=cls, value=value):
                    with self.assertRaisesRegex(ValueError, 'explicit uint8'):
                        statescript.write_payload(BitWriter(), cls, payload, 0, 16, full=False)
                    with self.assertRaisesRegex(ValueError, 'explicit uint8'):
                        statescript.write_remote_payload(BitWriter(), cls, payload, False)
            for value in CONTRACT['input']['initial_bytes']:
                state.placement_byte = value
                payload = placement_byte.payload(state)
                for owner in (True, False):
                    out = BitWriter()
                    if owner:
                        statescript.write_payload(out, cls, payload, 0, 16, full=False)
                    else:
                        statescript.write_remote_payload(out, cls, payload, False)
                    self.assertEqual((out.count, out.getvalue()), (8, bytes([value])))
            del state.placement_byte
            with self.assertRaises(AttributeError):
                placement_byte.payload(state)
            for owner in (True, False):
                with self.assertRaises(KeyError):
                    if owner:
                        statescript.write_payload(BitWriter(), cls, {}, 0, 16, full=False)
                    else:
                        statescript.write_remote_payload(BitWriter(), cls, {}, False)
