"""Actual Zen Q keeps a physical deadline when its owner epoch differs."""
import json
import unittest
from pathlib import Path

from ow174.game import combat, zenyatta
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim


class ZenyattaUltimateWorldClockTests(unittest.TestCase):
    def test_actual_q_expires_in_world_time_and_allows_the_next_rifle_hit(self):
        fixture = json.loads((Path(__file__).parents[2] / 'fixtures/behavior'
                              / 'zenyatta-ultimate-world-clock-v1.json').read_text(encoding='utf-8'))
        inputs, expected = fixture['input'], fixture['expected']
        run = Run(names=('Zen', 'Soldier'))
        zen, soldier = run.players
        bots = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(bots), 15)
        zen.commands.last_frame = inputs['owner_birth']
        run.match.switch_hero(zen, heroes()[zenyatta.HERO])
        self.assertEqual(zen.body_script.world_frame_anchor,
                         (inputs['world_birth'], inputs['owner_birth']))
        x, y, z = zen.mover.position
        soldier.mover.position[:] = [x, y, z + 8]
        angles = aim(run.match.combat._eye(soldier), (x, y + .5, z))
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        zen.take_commands([Command(inputs['owner_birth'] + 1, buttons=0x20)])
        soldier.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1])])
        manager = run.match.combat.zenyatta
        self.assertTrue(manager.ultimate(zen))
        self.assertEqual(manager.ultimate_last_world_tick(zen), expected['last_world_tick'])
        health = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is zen))
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        soldier.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=FIRE)])
        self.assertEqual(health.total, expected['protected_health'])
        self.assertFalse(soldier.client.messages(combat.HIT))
        last = expected['last_world_tick']
        run.match.update(last * combat.TICK, last)
        soldier.take_commands([Command(last, yaw=angles[0], pitch=angles[1])])
        self.assertTrue(manager.ultimate(zen))
        self.assertEqual(health.total, expected['protected_health'])
        run.match.update((last + 1) * combat.TICK, last + 1)
        self.assertFalse(manager.ultimate(zen))
        self.assertEqual(script.frame, inputs['owner_birth'] + 1)
        soldier.take_commands([Command(last + 1, yaw=angles[0], pitch=angles[1], buttons=FIRE)])
        self.assertEqual(health.total, expected['after_expiry_health'])
        self.assertEqual([m['+0x78']['+0x4'] for m in soldier.client.messages(combat.HIT)], [20])
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), bots)
