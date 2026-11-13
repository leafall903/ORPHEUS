"""Actual moving recipient commands preserve delayed rivet collision and HIT labels."""
import json
import unittest
from pathlib import Path

from test_game_torbjorn import TorbjornProjectileTests

from ow174.game import combat
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/torbjorn-primary-moving-target-v1.json').read_text())


class TorbjornPrimaryMovingTargetTests(unittest.TestCase):
    def replay(self, packet_size, separate_epoch):
        run, shooter, victim = TorbjornProjectileTests().arena(CASE['input']['distance'])
        if separate_epoch:
            run.match.switch_hero(shooter, shooter.hero, preserve_pose=True, start_frame=9000)
            shooter.commands.reset(last_frame=9000)
            for offset in range(1, 21):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                shooter.take_commands([Command(9000 + offset)])
                victim.take_commands([Command(run.frame)])
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is victim)
        start, owner_start = run.frame, shooter.commands.last_frame
        pending = []
        for offset in range(1, CASE['input']['sample_frames'] + 1):
            tick = start + offset
            run.match.update(tick * combat.TICK, tick)
            victim.take_commands([Command(tick, right=CASE['input']['target_right'])])
            pending.append(Command(owner_start + offset, buttons=1 if offset == 1 else 0))
            if offset % packet_size == 0:
                shooter.take_commands(pending)
                pending = []
        self.assertFalse(pending)
        return {'health': fight.vitals_of(target).total,
                'hits': shooter.client.messages(combat.HIT), 'live': len(fight.rockets),
                'recipient_position': tuple(victim.mover.position)}

    def test_delayed_rivet_uses_physical_recipient_pose_with_same_full_hit_payload(self):
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(packet, separate_epoch)
                                   for packet in CASE['input']['source_packet_sizes']]
                self.assertEqual(direct, delayed)
                self.assertEqual(direct['health'], 200 - CASE['expected']['damage'])
                self.assertEqual(len(direct['hits']), CASE['expected']['hit_notices'])
                self.assertEqual(direct['live'], CASE['expected']['live_projectiles'])
