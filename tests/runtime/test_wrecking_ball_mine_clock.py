"""Minefield event clocks through accepted production commands with packet batching."""

import json
import unittest
from pathlib import Path

import test_delivered_ball_mine_factory as mine_tests
import test_delivered_wrecking_ball_combat as ball_tests
from test_delivered_wrecking_ball_fireball import relocate
from test_delivered_wrecking_ball_piledriver import lift

from ow174.game import combat, world
from ow174.game.commands import Command
from ow174.game.script.driver import ULT_COST


class MineClockTests(unittest.TestCase):
    setUpClass = classmethod(mine_tests.MineTests.setUpClass.__func__)

    def test_piledriver_air_lock_uses_landing_event_in_each_command_batch(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/ball-mine-clock-v1.json').read_text())
        for batch in (1, 8, 32):
            with self.subTest(batch=batch):
                run, player, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
                lift(run, player)
                start = run.frame
                pending = []
                for tick in range(start + 1, start + 97):
                    run.match.update(tick * combat.TICK, tick)
                    pending.append(Command(tick, buttons=128 if tick == start + 1 else 0))
                    if len(pending) == batch:
                        player.take_commands(pending)
                        pending = []
                fight = run.match.combat
                self.assertEqual(fight.vitals_of(target).total, 100)
                expected_lock = tuple(fixture['expected']['piledriver_air_lock'])
                self.assertEqual(fight.pile_air_locks[enemy.body], expected_lock)
                self.assertEqual(player.client.messages(combat.HIT)[0]['+0x84'],
                                 fixture['expected']['piledriver_landing_frame'])

    def observe(self, batch):
        run, player, enemy = mine_tests.MineTests().scenario()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        start = run.frame
        pending = []
        samples = []
        created = set()
        first_hit = None
        fight = run.match.combat
        for tick in range(start + 1, start + 257):
            if tick == start + 128:
                mine = min(fight.mines.entities.values(), key=lambda item: item.entity)
                x, y, z = mine.position
                relocate(run, enemy, (x, y, z + 0.5))
            run.match.update(tick * combat.TICK, tick)
            pending.append(Command(tick, buttons=32 if tick == start + 1 else 0))
            if len(pending) == batch:
                player.take_commands(pending)
                pending = []
            if first_hit is None and player.client.messages(combat.HIT):
                first_hit = tick
            created.update(fight.mines.entities)
            if (tick - start) % 32 == 0:
                mines = sorted(fight.mines.entities.values(), key=lambda item: item.entity)
                samples.append(tuple((mine.reported_frame, mine.born_tick, mine.last_tick,
                                      mine.position, mine.velocity, mine.landed, mine.armed)
                                     for mine in mines))
        target = next(target for target in fight._targets() if target.owner is enemy)
        destroyed = tuple(item.entity for item in player.client.entities
                          if item.op == world.OP_DESTROY and item.entity in created)
        return samples, first_hit, fight.vitals_of(target).total, destroyed

    def test_same_cast_flight_landing_hit_and_retirement_across_command_batches(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/ball-mine-clock-v1.json').read_text())
        expected = fixture['expected']
        baseline = self.observe(1)
        samples, first_hit, health, destroyed = baseline
        self.assertEqual(len(samples[0]), expected['mine_count_at_first_query'])
        self.assertEqual(samples[0][0][:3], (expected['first_reported_frame'],
                                          expected['first_born_tick'], expected['first_last_tick']))
        self.assertEqual(samples[0][0][3][1], expected['first_mine_height_at_first_query'])
        self.assertIsNotNone(first_hit)
        self.assertEqual(health, expected['target_health_after_explosion'])
        self.assertEqual(len(destroyed), expected['destroyed_mines'])
        for batch in fixture['input']['command_batches'][1:]:
            with self.subTest(batch=batch):
                self.assertEqual(self.observe(batch), baseline)
