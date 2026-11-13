"""Actual Sentry damage retains authored tags into the existing victim service."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_symmetra_combat import fire, health
from test_symmetra_turrets import SentryTests, wall

from ow174.game import combat, sombra_camo
from ow174.game.content import heroes
from ow174.game.script import expr


class SentryDamageTagTests(unittest.TestCase):
    def test_actual_beam_preserves_tags_at_received_damage_without_extra_damage(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                               / 'symmetra-sentry-damage-tags-v1.json').read_text())
        fixture = SentryTests()
        run, owner, enemy = fixture.setup_arena()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[sombra_camo.HERO]))
        enemy.mover.position[:] = (2, 1, 1)
        owner.body_script.component.set(12918, 0)
        before = health(run, enemy).total
        expected = tuple(expr.Asset(int(value, 16)) for value in contract['expected']['health_tags'])
        fight = run.match.combat
        with self.assertNoLogs('ow174.game', level='ERROR'), \
                patch.object(fight, '_collision', return_value=wall()), \
                patch.object(sombra_camo, 'queue_damage', wraps=sombra_camo.queue_damage) as receipts:
            fixture.launch(run, owner)
            fire(run, owner, 80, buttons=0)
        sentry, = fight.sentries.sentries
        self.assertEqual(sentry.target, enemy.body)
        loss = before - health(run, enemy).total
        self.assertGreater(loss, 0)
        self.assertAlmostEqual(loss, contract['expected']['damage_per_second']
                               * (fight.now - (sentry.attached_at + .5)), places=4)
        self.assertAlmostEqual(owner.body_script.component.entity_var(342).value(), loss, places=4)
        self.assertTrue(receipts.call_args_list)
        self.assertEqual(len(owner.client.messages(combat.HIT)), len(receipts.call_args_list))
        for call in receipts.call_args_list:
            self.assertIs(call.args[0], enemy.body_script)
            self.assertEqual(call.args[1][contract['expected']['received_damage_parameter']], expected)
            self.assertEqual(call.args[2], owner.body)
