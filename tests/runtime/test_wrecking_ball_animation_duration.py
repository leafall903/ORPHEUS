"""Synthetic resource traversal and actual Roll timing; native appearance pending."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_delivered_wrecking_ball_combat as ball_combat

from ow174.game import ball
from ow174.game.script import expr

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from extract_emote_durations import body_animation_records, extract, greeting_records

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/ball-model-animation-duration-v1.json'


class RollAnimationDurationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.inputs = self.fixture['input']

    def resource(self, directory, seconds, child=2):
        """Synthetic compact STU XML; shared extractor reads it without adapters."""
        data = self.inputs
        path = Path(directory)
        (path / '00000000084A.021.xml').write_text(f'''<STUAnimBlendTreeSet
 xmlns:d="https://yretenai.com/dragonml/v1">
 <d:array d:name="m_253EE7C8"><UInt64>{int(data['alias'], 16)}</UInt64></d:array>
 <d:array d:name="m_blendTreeItems"><STUAnimBlendTreeSet_BlendTreeItem>
 <STU_7D00A73D d:name="m_gameData"><d:array d:name="m_nodes">
 <STU_623B2B19 m_E6058AAD="1"><d:array d:name="m_9F7CA0BB">
 <UInt32>2147483758</UInt32><UInt32>0</UInt32></d:array>
 <d:array d:name="m_540E2FC8"><Byte>1</Byte><Byte>3</Byte></d:array></STU_623B2B19>
 <STU_30C2E3AE m_38CF6CD3="{child}"/><STU_3C8FB392 m_9D16E038="0"/>
 <UnsupportedNode/></d:array><d:array d:name="m_animDatas">
 <STUAnimGameData_AnimationData m_D8514AD9="{seconds}"/>
 </d:array></STU_7D00A73D></STUAnimBlendTreeSet_BlendTreeItem></d:array>
 </STUAnimBlendTreeSet>''', encoding='utf-8')
        body = path / '00000000200B.003.xml'
        body.write_text('''<STUEntityDefinition xmlns:d="https://yretenai.com/dragonml/v1">
 <STUModelComponent><d:ref d:name="m_animBlendTreeSet" GUID="00000000084A.021"/>
 </STUModelComponent></STUEntityDefinition>''', encoding='utf-8')
        rows = extract(path)
        return body_animation_records(rows, [body])

    def test_nested_ik_body_context_preserves_arbitrary_resource_duration(self):
        for seconds in self.inputs['synthetic_durations_seconds']:
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as directory:
                records = self.resource(directory, seconds)
                result = records[self.inputs['body']][self.inputs['alias']][self.inputs['category']]
                self.assertEqual(result, seconds)
                # Greeting filtering still rejects a table without its required aliases.
                with self.assertRaisesRegex(ValueError, 'missing greeting durations'):
                    greeting_records(extract(Path(directory)), [Path(directory) / '00000000200B.003.xml'])

    def test_invalid_or_cyclic_nested_ik_stays_unresolved(self):
        for child in (1, 99):
            with self.subTest(child=child), tempfile.TemporaryDirectory() as directory:
                records = self.resource(directory, .192, child)
                self.assertEqual(records, {})

    def test_real_roll_finishes_intro_at_extracted_duration_and_keeps_loop(self):
        expected = self.fixture['expected']
        for seconds, first_loop in zip(self.inputs['synthetic_durations_seconds'],
                                       expected['first_loop_frames'], strict=True):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as directory:
                table = Path(directory) / 'durations.json'
                table.write_text(json.dumps(self.resource(directory, seconds)), encoding='utf-8')
                with patch.dict(os.environ, {'ORPHEUS_BALL_ANIMATION_DURATIONS': str(table)}):
                    run, player, *_ = ball_combat.BallCombatTests().scenario()
                    script = player.body_script
                    graph = ball.instance(player, 0x1203)
                    self.assertEqual(script.world.animation_duration(graph, graph.graph.node(43)),
                                     expr.f32(seconds))
                    for frame in range(1, first_loop + 3):
                        run.step({player: (0, 0, 8)} if frame == 1 else {})
                        graph = ball.instance(player, 0x1203)
                        active = {s.node.pos for s in graph.states.values() if s.active}
                        self.assertEqual(43 in active, frame < first_loop,
                                         (frame, graph.node_state(graph.graph.node(43)).start,
                                          graph.component.now))
                        self.assertEqual(42 in active, frame >= first_loop)
                        self.assertEqual(ball.rolling(player), expected['roll_remains_active'])
                    self.assertEqual(script.world.animation_duration(graph, graph.graph.node(43)),
                                     expr.f32(seconds))

    def test_missing_table_and_other_context_have_no_fallback_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.json'
            with patch.dict(os.environ, {'ORPHEUS_BALL_ANIMATION_DURATIONS': str(path)}):
                run, player, *_ = ball_combat.BallCombatTests().scenario()
                run.step({player: (0, 0, 8)})
                graph = ball.instance(player, 0x1203)
                world = player.body_script.world
                self.assertIsNone(world.animation_duration(graph, graph.graph.node(43)))
                self.assertIs(ball.animation_duration(world, graph, graph.graph.node(44)), NotImplemented)
                table = Path(directory) / 'populated.json'
                table.write_text(json.dumps(self.resource(directory, .192)), encoding='utf-8')
                with patch.dict(os.environ, {'ORPHEUS_BALL_ANIMATION_DURATIONS': str(table)}):
                    with patch.object(world, 'body_definition', 0x0400000000000001):
                        self.assertIsNone(world.animation_duration(graph, graph.graph.node(43)))
                    with patch.object(world, 'hero', 0x02E0000000000076):
                        self.assertIs(ball.animation_duration(world, graph, graph.graph.node(43)),
                                      NotImplemented)
                    with patch.object(graph.component, 'strict', True):
                        self.assertIs(ball.animation_duration(world, graph, graph.graph.node(43)),
                                      NotImplemented)


if __name__ == '__main__':
    unittest.main()
