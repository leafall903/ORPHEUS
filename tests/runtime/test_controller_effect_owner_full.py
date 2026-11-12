"""Original controller effect wires share the existing owner full-frame encoder."""
import unittest

from ow174.game import heroselect, statescript
from ow174.game.script import decode, graph


class ControllerEffectOwnerFullTests(unittest.TestCase):
    def definition(self):
        if not graph.GRAPHS_PATH.is_file():
            self.skipTest('requires original1177 graph')
        definition = graph.graph(0x1177)
        if definition is None:
            self.skipTest('requires original1177 graph')
        return definition

    def test_original_linked_payload_and_bound_flag_share_static_controller_frame(self):
        definition = self.definition()
        static, variables = heroselect.controller_instances(heroselect.PRACTICE, False)
        linked, flag = definition.node(9), definition.node(46)
        payload = {'has_entity': True, 'entity': 0x80001234,
                   'values': [{'set': False}] * 7}
        item = statescript.InstanceWire(20, definition, states={
            linked.state: statescript.StateWire(linked.cls, True, payload),
            flag.state: statescript.StateWire(flag.cls, True, {'top': True}),
        })
        binding = statescript.Binding(item.id, flag.state, 0, 0, False)
        frame = statescript.owner_full_frame(100, static, variables, dynamic=(item,),
                                            bound_vars={14695: (True, (binding,))})
        chunk = statescript.chunk(frame, 0, 1)
        decoded = decode.read_chunk(chunk.getvalue(), chunk.count)
        self.assertEqual(set(decoded.instances), {entry.index for entry in static} | {20})
        self.assertEqual(decoded.instances[20].states[linked.state], (True, payload))
        value, bindings = decoded.entity_vars[14695]
        self.assertIs(value, True)
        self.assertEqual([(entry.instance, entry.state) for entry in bindings], [(20, flag.state)])

    def test_dynamic_effect_cannot_overwrite_existing_controller_namespace(self):
        definition = self.definition()
        static, variables = heroselect.controller_instances(heroselect.PRACTICE, False)
        collision = statescript.InstanceWire(static[0].index, definition)
        with self.assertRaises(ValueError):
            statescript.owner_full_frame(100, static, variables, dynamic=(collision,))
        with self.assertRaises(ValueError):
            statescript.owner_full_frame(100, [], {14695: statescript.Bool(True)},
                                        bound_vars={14695: (False, ())})
