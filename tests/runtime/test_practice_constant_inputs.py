"""Constant mode inputs preserve live variable subscriptions and host scope."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game.content import PRACTICE_RANGE_MODE, SOLDIER
from ow174.game.script import runtime
from ow174.game.script.bot import BotWorld
from tests.script_fixtures import graph, node


class PracticeConstantInputsTests(unittest.TestCase):
    def test_mode_constant_keeps_live_condition_changes(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/practice-constant-inputs-v1.json'
        fixture = json.loads(path.read_text())
        inputs, expected = fixture['input'], fixture['expected']
        cases = [(False, expected['eligible_heroes'][0])]
        cases.extend((True, hero) for hero in expected['eligible_heroes'])
        for enabled, hero in cases:
            world = BotWorld(0x02E0000000000000 | hero, PRACTICE_RANGE_MODE, {})
            component = runtime.Component(0xC0000001, world=world)
            definition = graph([node(0, 'STUStatescriptStateBooleanSwitch', state=0, m_condition={
                '$': 'STU_919FD47C',
                'm_configVars': [{'$': 'STU_A77AC2AC'}],
                'm_expression': {
                    '$': 'STUConfigVarExpressionData', 'm_opcodes': [14, 0, 3, 4, 8, 0, 0],
                    'm_dynamicVars': [{'$': 'STUConfigVarDynamic',
                                      'm_identifier': inputs['entity_variable'], 'm_60DB8F99': 1}],
                },
            })])
            instance = component.create(definition, init=False)
            instance.initialized = True
            state = instance.state(0)
            resolve = world.constant_native if enabled else lambda *args: NotImplemented
            with patch.object(world, 'constant_native', resolve):
                state.begin()
                observed = []
                for step, value in enumerate(inputs['values']):
                    component.set(inputs['entity_variable'], value)
                    for frame in range(inputs['frames_between_changes']):
                        component.run_frame(1 + step * inputs['frames_between_changes'] + frame)
                    observed.append(state.current)
                self.assertEqual(observed, expected['switch_values'])
                self.assertIs(state.polled, not enabled)
                self.assertIn(state, component.entity_var(inputs['entity_variable']).watchers)

    def test_other_heroes_and_hosts_keep_their_native_path(self):
        config = {'$': 'STU_A77AC2AC'}
        for world in (BotWorld(SOLDIER, PRACTICE_RANGE_MODE, {}),
                      BotWorld(0x02E000000000016B, 0, {})):
            self.assertIs(world.constant_native(config, None), NotImplemented)
        world = BotWorld(0x02E000000000016B, PRACTICE_RANGE_MODE, {})
        self.assertIs(world.constant_native({'$': 'STU_9B7648C3'}, None), NotImplemented)
        world.control_context = object()
        self.assertIs(world.constant_native(config, None), NotImplemented)
        self.assertIs(runtime.World().constant_native(config, None), NotImplemented)
