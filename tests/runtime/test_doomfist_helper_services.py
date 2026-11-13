"""Actual recipient helper queries; no full-helper or native acceptance claim."""

import json
import os
import unittest
from pathlib import Path

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat, defenses, doomfist_helper, npc, npc_console
from ow174.game.content import heroes
from ow174.game.script import expr, graph

FIXTURES = Path(os.environ.get('ORPHEUS_BEHAVIOR_FIXTURES',
                              Path(__file__).resolve().parents[2] / 'fixtures/behavior'))
CONTRACT = json.loads((FIXTURES / 'doomfist-helper-response-v1.json').read_text(encoding='utf-8'))


class HelperResponseTests(DoomfistFixture, unittest.TestCase):
    def test_original_helper_velocity_query_uses_existing_recipient_mover(self):
        component = self.b.body_script.component
        helper = component.create(graph.graph(0x0D07), init=False)
        helper.init(run_entries=False)
        helper.set_source_entity(self.a.body)
        # Use the original physical-velocity config, wherever this graph embeds it.
        def configs(value):
            if isinstance(value, dict):
                if value.get('$') == 'STU_309B0570':
                    yield value
                for child in value.values():
                    yield from configs(child)
            elif isinstance(value, list):
                for child in value:
                    yield from configs(child)
        query = next(config for node in helper.graph.nodes for config in configs(node.fields)
                     if config.get('m_5B94EDB8') is None)
        mover = self.b.mover
        self.a.mover.velocity = (50.0, 60.0, 70.0)
        mover.velocity = tuple(CONTRACT['input']['recipient_velocity'])
        serial = self.fight.knockbacks.serial
        self.assertEqual(helper.evaluate(query), expr.Vec3(*CONTRACT['input']['recipient_velocity']))
        mover.velocity = (4.0, -2.0, 1.0)
        self.assertEqual(helper.evaluate(query), expr.Vec3(4.0, -2.0, 1.0))
        self.assertIs(self.b.mover, mover)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        component.destroy(helper)

    def test_original_helper_condition_reads_live_recipient_response(self):
        self.match.switch_hero(self.b, heroes()[defenses.ORISA])
        component = self.b.body_script.component
        helper = component.create(graph.graph(0x0D07), init=False)
        helper.init(run_entries=False)
        helper.set_source_entity(self.a.body)
        helper.write((expr.INSTANCE, 40), expr.Entity(self.a.body))
        query = helper.graph.node(66).field('m_condition')['m_configVars'][0]
        condition = helper.graph.node(66).field('m_condition')
        before = self.vitals().total
        serial = self.fight.knockbacks.serial
        hits = len(self.a.client.messages(combat.HIT))
        self.a.body_script.component.set(10959, 0)
        self.assertEqual(helper.evaluate(query), CONTRACT['expected']['ordinary'])
        self.assertTrue(helper.evaluate(condition))
        self.run.step({self.b: (0, 0, 8)})
        self.assertEqual(defenses.knockback_scale(self.b), 0.0)
        self.assertEqual(helper.evaluate(query), CONTRACT['expected']['fortify'])
        self.assertFalse(helper.evaluate(condition))
        self.a.body_script.component.set(10959, 5)
        self.assertEqual(helper.evaluate(query), 0.0)
        for _ in range(280):
            self.run.step({})
        self.assertEqual(helper.evaluate(query), 1.0)
        self.assertTrue(helper.evaluate(condition))
        self.assertEqual(self.vitals().total, before)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), hits)
        self.assertFalse(any(state.active for state in helper.states.values()))
        component.destroy(helper)

    def test_actual_heavy_recipient_and_retired_player_have_no_response_fallback(self):
        definition = next(d for d in npc_console.configured_catalog().values() if d.name == 'Heavy Assault')
        heavy = npc.spawn(self.match, definition, (1000, 0, 1003), team=1)
        for _ in range(3):
            self.run.step({})
        component = heavy.body_script.component
        helper = component.create(graph.graph(0x0D07), init=False)
        helper.init(run_entries=False)
        query = helper.graph.node(66).field('m_condition')['m_configVars'][0]
        self.assertEqual(helper.evaluate(query), CONTRACT['expected']['heavy'])
        self.assertFalse(helper.evaluate(helper.graph.node(66).field('m_condition')))
        velocity_query = {'$': 'STU_309B0570', 'm_entity': {'$': 'STU_B5A0CAF0'}, 'm_5B94EDB8': None}
        self.assertIs(doomfist_helper.native(component.world, velocity_query, helper), expr.EVALUATION_FAILED)
        self.assertIsNone(heavy.impulse_mover)
        component.destroy(helper)
        old_component = self.b.body_script.component
        helper = old_component.create(graph.graph(0x0D07), init=False)
        helper.init(run_entries=False)
        world = old_component.world
        self.match.switch_hero(self.b, heroes()[defenses.ORISA])
        self.assertIs(doomfist_helper.response(world, query, helper), expr.EVALUATION_FAILED)
        self.assertIs(doomfist_helper.response(world, {'$': 'STU_71A87E92'}, helper), NotImplemented)


if __name__ == '__main__':
    unittest.main()
