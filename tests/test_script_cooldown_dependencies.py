"""Cooldown dependency and restart regressions; portable lane0007 fixtures reviewed here."""

import unittest

from ow174.game.script import graph, runtime
from ow174.game.script import nodes as _nodes  # noqa: F401 -- register state implementations


def definition(classes):
    return graph.Graph({'index': 0xF001, 'header': {}, 'fields': {}, 'items': len(classes),
                        'containers': {}, 'unknown_nodes': [], 'entries': [], 'remote': [], 'sync_vars': [],
                        'states': list(range(len(classes))), 'nodes': [
                            {'pos': n, 'rid': n, 'uid': n, 'state': n, 'remote': -1, 'class': cls,
                             'client_only': False, 'server_only': False, 'fields': fields}
                            for n, (cls, fields) in enumerate(classes)]})


def variable(identifier):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': 0}


class SharedRuntimeTests(unittest.TestCase):
    def test_reactivated_switch_observes_changes_after_queued_callback_was_cancelled(self):
        component = runtime.Component(0xA0000101)
        instance = component.create(definition([
            ('STUStatescriptStateBooleanSwitch', {'m_condition': variable(77)})]))
        switch = instance.state(0)
        switch.begin()
        instance.write((0, 77), True)
        self.assertTrue(switch.flags_changed)
        switch.end(False)
        switch.begin()
        self.assertTrue(switch.current)
        instance.write((0, 77), False)
        component.run_now()
        self.assertFalse(switch.current)

    def test_computed_cooldown_expiry_notifies_a_waiting_ready_switch(self):
        condition = {'$': 'STU_919FD47C', 'm_expression': {
            '$': 'STUConfigVarExpressionData', 'm_opcodes': [8, 0, 6, 0, 37, 0],
            'm_dynamicVars': [variable(61)], 'm_D99EF254': [0]}}
        component = runtime.Component(0xA0000101)
        instance = component.create(definition([
            ('STUStatescriptStateAbility', {'m_out_CooldownVar': variable(61)}),
            ('STUStatescriptStateBooleanSwitch', {'m_condition': condition}),
        ]))
        instance.write((0, 61), 0.032)
        instance.state(0).begin()
        ready = instance.state(1)
        ready.begin()
        component.run_frame(0)
        self.assertFalse(ready.current)
        component.run_frame(2)
        self.assertEqual(instance.find_var(0, 61).value(), 0.0)
        self.assertTrue(ready.current)
