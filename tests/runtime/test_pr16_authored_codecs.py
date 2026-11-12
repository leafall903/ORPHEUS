"""PR #16 wire regressions that use the prepared 104319 codec table."""

import unittest
from unittest import mock

from ow174.game import statescript
from ow174.game.bits import BitReader, BitWriter
from ow174.game.script import decode, expr
from tests.public.test_script_registered_state import CLASS, model


class RegisteredStateWireTests(unittest.TestCase):
    def test_owner_delta_and_remote_full_delta_frames_supply_linked_instance_metadata(self):
        definition = model(count=3)
        graphs = {23: definition, 24: definition}
        payload = {'link': (23, 2)}
        wires = [statescript.InstanceWire(23, definition, states={
                    2: statescript.StateWire(CLASS, True, {})}),
                 statescript.InstanceWire(24, definition, states={
                    0: statescript.StateWire(CLASS, True, payload)})]
        frames = [(32, statescript.owner_delta(1000, True, wires, {}, graphs)),
                  (0, statescript.remote_full_frame(wires, {}, graphs)),
                  (32, statescript.remote_delta(wires, {}, graphs))]
        for first, frame in frames:
            with self.subTest(frame=frame.count):
                chunk = statescript.chunk(frame, first, 1)
                with mock.patch.object(decode, 'graph_of', return_value=definition):
                    result = decode.read_chunk(chunk.getvalue(), chunk.count, {23: 1, 24: 1})
                self.assertEqual(result.instances[24].states[0], (True, payload))

    def test_owner_and_remote_payloads_keep_cross_instance_links_and_use_distinct_indexes(self):
        definition = model(count=3)
        graphs = {23: definition}
        for owner in (True, False):
            for full in ((False,) if owner else (False, True)):
                with self.subTest(owner=owner, full=full):
                    out = BitWriter()
                    payload = {'link': (23, 2)}
                    if owner:
                        statescript.write_payload(out, CLASS, payload, 0, 16, graphs=graphs)
                    else:
                        statescript.write_remote_payload(out, CLASS, payload, full, graphs=graphs)
                    raw = BitReader(out.getvalue())
                    self.assertEqual(raw.bit(), 1)
                    self.assertEqual(decode.w_u16(raw), 23)
                    self.assertEqual(raw.bits(2), 2 if owner else 0)
                    reader = BitReader(out.getvalue())
                    with mock.patch.object(decode, 'graph_of', side_effect=lambda value: value):
                        self.assertEqual(decode.read_payload(reader, CLASS, 0, 16, owner, full,
                                                             graphs=graphs), payload)
                    self.assertEqual(reader.pos, out.count)

    def test_missing_unknown_nonnetworked_and_unrepresentable_links_are_rejected(self):
        for payload, graphs in [
            ({'link': (23, 0)}, None), ({'link': (23, 9)}, {23: model()}),
            ({'link': (23, 0)}, {23: model(server_only=(0,))}),
            ({'link': (0, 0)}, {0: model()}), ({'link': (23, True)}, {23: model()}),
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                statescript.write_payload(BitWriter(), CLASS, payload, 0, 16, graphs=graphs)
        with self.assertRaises(ValueError):
            statescript.write_remote_payload(BitWriter(), CLASS, {'link': (23, 0)}, False,
                                             graphs={23: model(remote=False)})

    def test_absent_link_still_writes_the_native_single_zero_bit(self):
        for owner in (True, False):
            out = BitWriter()
            if owner:
                statescript.write_payload(out, CLASS, {}, 0, 16)
            else:
                statescript.write_remote_payload(out, CLASS, {}, False)
            self.assertEqual((out.value, out.count), (0, 1))


class MessageWireTests(unittest.TestCase):
    def test_wire_payload_encodes_sender_kind_and_stacked_flag(self):
        for sender in (0, 0x10000101, 0x80000101, 0xA0000101, 0xFFFFFFFF):
            for stacked in (False, True):
                out = BitWriter()
                statescript.write_payload(out, 'STUStatescriptStateGameMessageEntry',
                    {'sender': sender, 'stacked': stacked}, 0, 16)
                reader = BitReader(out.getvalue())
                valid = expr.valid_entity(sender) and bool(sender & 0x80000000)
                self.assertEqual(bool(reader.bit()), valid)
                if valid:
                    self.assertEqual(bool(reader.bit()), sender & 0xA0000000 == 0xA0000000)
                    self.assertEqual(reader.var_b(), sender & 0x1FFFFFFF)
                self.assertEqual(bool(reader.bit()), stacked)

    def test_reply_payload_round_trips_variable_width_unsigned16_values(self):
        for value in (0, 1, 63, 64, 255, 256, 65535, 0x10000):
            out = BitWriter()
            statescript.write_payload(out, 'STU_38EE1100', {'reply': value}, 0, 16)
            reader = BitReader(out.getvalue())
            result = decode.read_payload(reader, 'STU_38EE1100', 0, 16, owner=True, full=False)
            self.assertEqual(result, {'reply': value & 0xFFFF} if value & 0xFFFF else {})
            self.assertEqual(reader.pos, out.count)
