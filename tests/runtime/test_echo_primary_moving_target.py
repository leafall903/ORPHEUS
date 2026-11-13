"""Actual target commands discriminate physical-tick collision from receipt-time collision."""
import json
import unittest
from pathlib import Path

from test_echo_combat import EchoPrimaryTests

from ow174.game import combat
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/echo-primary-moving-target-v1.json').read_text())


class EchoPrimaryMovingTargetTests(unittest.TestCase):
    def replay(self, packet_size, separate_epoch):
        run, shooter, victim, target, angles = EchoPrimaryTests().session(
            distance=CASE['input']['distance'])
        if separate_epoch:
            run.match.switch_hero(shooter, shooter.hero, preserve_pose=True, start_frame=9000)
            shooter.commands.reset(last_frame=9000)
            for offset in range(1, 21):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                shooter.take_commands([Command(9000 + offset)])
                victim.take_commands([Command(run.frame)])
        world_start, owner_start = run.frame, shooter.commands.last_frame
        pending = []
        for offset in range(1, CASE['input']['sample_frames'] + 1):
            tick = world_start + offset
            run.match.update(tick * combat.TICK, tick)
            victim.take_commands([Command(tick, right=CASE['input']['target_right'])])
            pending.append(Command(owner_start + offset, yaw=angles[0], pitch=angles[1],
                                   buttons=1 if offset == 1 else 0))
            if offset % packet_size == 0:
                shooter.take_commands(pending)
                pending = []
        hits = shooter.client.messages(combat.HIT)
        return {'health': run.match.combat.vitals_of(target).total,
                'hits': hits, 'live': len(run.match.combat.rockets)}

    def test_packet_delay_preserves_moving_target_damage_and_owner_hit_labels(self):
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(size, separate_epoch)
                                   for size in CASE['input']['source_packet_sizes']]
                self.assertEqual(direct, delayed)
                self.assertEqual(direct['health'], 200 - CASE['expected']['damage'])
                self.assertEqual(len(direct['hits']), CASE['expected']['hit_notices'])
                self.assertEqual(direct['live'], CASE['expected']['live_projectiles'])
