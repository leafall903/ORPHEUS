"""Actual mine/Recall inputs consume0073's authored poison end flag."""
import json
import unittest
from pathlib import Path

from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import combat, tracer
from ow174.game.content import heroes
from ow174.game.script import expr


class RecallVenomTests(_WidowmakerMatch, unittest.TestCase):
    def test_actual_recall_ends_existing_poison_and_reveal_without_resuming_damage(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              'fixtures/behavior/tracer-recall-venom-v1.json').read_text(encoding='utf-8'))
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[tracer.HERO], preserve_pose=True))
        fight = self.run.match.combat
        mines = fight.venom
        self.press(0x10)  # Widow's actual mine throw; no fabricated Poison record.
        for _ in range(160):
            if mines.poison:
                break
            self.press(0)
        self.assertEqual(len(mines.poison), 1)
        self.assertEqual(mines.poison[0].target, self.b.body)
        self.assertTrue(mines.poisoned_by(self.a, self.b.body))
        self.press(0, 8)  # Integer poison pulses accumulate over actual ticks.
        self.assertLess(self.health(self.b), 150)
        self.press(0x10, attacker=self.b, defender=self.a)  # Tracer's actual Recall.
        self.assertTrue(tracer.recalling(self.b))
        for _ in range(60):
            flag = self.b.body_script.component.entity_var(fixture['recall_end_flag'], create=False)
            if flag is not None and expr.truthy(flag.value()):
                break
            self.press(0)
        self.assertTrue(expr.truthy(flag.value()), 'original Recall8440 must actually be active')
        self.press(0)  # Let the real poison producer observe the authored flag.
        self.assertFalse(mines.poison, '0073 end flag must retire poison, not just suppress a damage pulse')
        self.assertFalse(mines.poisoned_by(self.a, self.b.body))
        self.assertFalse(fight.revealed_to(self.a, self.b.body))
        for _ in range(100):
            if not tracer.recalling(self.b):
                break
            self.press(0)
        self.assertFalse(tracer.recalling(self.b))
        self.assertEqual(self.health(self.b), fixture['restored_health'])
        hits = len(self.a.client.messages(combat.HIT))
        self.press(0, 20)
        self.assertEqual(self.health(self.b), fixture['restored_health'])
        self.assertEqual(len(self.a.client.messages(combat.HIT)), hits)
        self.press(1)  # Later real rifle damage remains enabled.
        self.assertLess(self.health(self.b), fixture['restored_health'])
