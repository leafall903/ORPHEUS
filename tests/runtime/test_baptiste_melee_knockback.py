"""Authored Baptiste melee force trigger; native recipient adoption remains pending."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import knockback
from ow174.game.content import heroes
from tests.runtime import test_baptiste_combat as fixture


class BaptisteMeleeKnockbackTests(unittest.TestCase):
    def test_actual_melee_applies_authored_enemy_impulse_once(self):
        contract = json.loads((Path(__file__).resolve().parents[2]
                               / 'fixtures/behavior/baptiste-melee-impulse-v1.json').read_text())
        run, source, ally, enemy, fight, _, _ = fixture.BaptisteCombatTests().setup_fight()
        run.match.switch_hero(enemy, heroes()[fixture.BAPTISTE])
        for player, point in ((source, (0, 1, 0)), (ally, (0, 1, 1.5)), (enemy, (0, 1, 1.5))):
            player.mover.body = None
            player.mover.position[:] = point
        for _ in range(30):
            run.step({})
        melee = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x43)
        self.assertEqual(knockback.quick_melee_ring(melee.state(4)), tuple(contract['input']['ring']))
        start = enemy.mover.position[2]
        impact_tick = run.frame + 1
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as apply:
            for frame in range(40):
                run.step({source: (0, 0, 0x800 if frame == 0 else 0)})
        expected = contract['expected']
        self.assertEqual(apply.call_count, expected['applications'])
        self.assertIs(apply.call_args.args[0].owner, enemy)
        self.assertEqual(tuple(apply.call_args.args[1]), tuple(expected['impulse']))
        self.assertEqual(apply.call_args.kwargs,
                         {'impact_tick': impact_tick, 'source_entity': source.body})
        targets = {target.owner: target for target in fight._targets()}
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 200 - expected['enemy_damage'])
        self.assertEqual(fight.vitals_of(targets[ally]).total, 200 - expected['ally_damage'])
        self.assertEqual(fight.knockbacks.serial, expected['applications'])
        self.assertGreater(enemy.mover.position[2], start)
