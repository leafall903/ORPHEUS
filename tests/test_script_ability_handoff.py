"""Queued cleanup refills a weapon before its volley samples the new resource.

This fixture already passes the existing arbitration order. It is a contract
check, not proof of the native cross-object order or a reason to change it.
"""

import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import nodes, runtime


def variable(identifier):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': 1}


def priority(index):
    return {'$': 'STU_8A491388', 'm_priority': f'0x{0x0C40000000000000 | index:016X}'}


def scope(state):
    return {'$': 'STU_904BFCEC', 'm_FF3DAF1E': [state], 'links': [[state, 'm_beginPlug']]}


class AbilityHandoffTests(unittest.TestCase):
    def test_outgoing_cleanup_refill_is_sampled_by_newly_unblocked_volley(self):
        definition = make_graph(0xFFFF, [
            node(0, 'STUStatescriptStateAbility', state=0, fields={
                'm_out_StackVar': variable(100), 'm_5E561E77': priority(3),
                'm_3C0EB4A6': priority(3), 'm_343E99EE': priority(3),
                'm_B969AFC9': scope(3),
            }),
            node(1, 'STUStatescriptStateStack', state=1, fields={
                'm_out_Var': variable(100), 'm_priority': priority(1),
                'm_value': {'$': 'STUConfigVarBool', 'm_value': True},
                'm_topSubgraphPlug': scope(2),
            }),
            node(2, 'STUStatescriptStateWeaponVolley', state=2, fields={
                'm_24E351A4': variable(53),
                'm_A17BE89B': {'$': 'STUConfigVarInt', 'm_value': 6},
                'm_A55BC904': {'$': 'STUConfigVarInt', 'm_value': 1},
                'm_numShotsPerSecond': {'$': 'STUConfigVarInt', 'm_value': 10},
            }),
            node(3, state=3, fields={'m_onAbortPlug': output([4, 'm_inPlug'])}),
            node(4, 'STUStatescriptActionSetVar', fields={
                'm_out_Var': variable(53), 'm_value': {'$': 'STUConfigVarInt', 'm_value': 6},
            }),
        ], states=[0, 1, 2, 3])
        component = runtime.Component(1, strict=True)
        instance = component.create(definition, init=False)
        instance.initialized = True
        component.set(53, 0)
        outgoing, incoming = instance.state(0), instance.state(1)
        outgoing.begin()
        outgoing.activate()
        incoming.begin()  # lower-priority weapon remains underneath the outgoing ability
        component.run_now()
        self.assertTrue(instance.state(3).active)
        self.assertFalse(instance.state(2).active)
        self.assertEqual(component.entity_var(53).value(), 0)
        outgoing.stop()
        component.run_now()
        volley = instance.state(2)
        self.assertIsInstance(volley, nodes.WeaponVolley)
        self.assertEqual(volley.ammo0, 6)
        self.assertEqual(volley.shots_planned(), 6)
        volley.report(component.now)
        self.assertEqual(len(component.shots), 1)
        self.assertEqual(component.entity_var(53).value(), 5)
