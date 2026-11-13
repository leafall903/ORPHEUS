"""Sticky contacts use retained body poses while the same real target starts moving."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_echo_combat import EchoPrimaryTests

from ow174.game import combat
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/echo-sticky-moving-contact-v1.json').read_text())


class EchoStickyMovingContactTests(unittest.TestCase):
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
        fight = run.match.combat
        explode, sweep = fight._explode, fight._sweep_rocket
        blasts, retained = [], []

        def observe_blast(rocket, point, **kwargs):
            if rocket.source_graph == 0x1C36:
                blasts.append((fight.tick, rocket.shot.frame, tuple(point), rocket.attached_entity))
            return explode(rocket, point, **kwargs)

        def observe_sweep(rocket, *args, **kwargs):
            if rocket.source_graph == 0x1C36 and fight.tick < fight.match.tick:
                history = fight.history[target.entity]
                retained.append(history[0][0] <= fight.tick <= history[-1][0])
            return sweep(rocket, *args, **kwargs)

        start, owner_start = run.frame, shooter.commands.last_frame
        pending = []
        with (patch.object(fight, '_explode', side_effect=observe_blast),
              patch.object(fight, '_sweep_rocket', side_effect=observe_sweep)):
            for offset in range(1, CASE['input']['sample_ticks'] + 1):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick)
                right = 127 if offset > CASE['input']['stand_ticks'] else 0
                victim.take_commands([Command(tick, right=right)])
                pending.append(Command(owner_start + offset, yaw=angles[0], pitch=angles[1],
                                       buttons=2 if offset == 1 else 0))
                if offset % packet_size == 0:
                    shooter.take_commands(pending)
                    pending = []
        if packet_size > 1:
            self.assertTrue(retained)
            self.assertTrue(all(retained), 'Do not pass by substituting a future sample for an evicted pose')
        return {'health': fight.vitals_of(target).total, 'hits': shooter.client.messages(combat.HIT),
                'blasts': blasts, 'live': len(fight.rockets)}

    def test_delayed_contacts_preserve_damage_hits_and_blast_geometry(self):
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(size, separate_epoch)
                                   for size in CASE['input']['packet_sizes']]
                self.assertEqual(direct, delayed)
                self.assertEqual(direct['health'], 200 - CASE['expected']['damage'])
                self.assertEqual(len(direct['hits']), CASE['expected']['hits'])
                self.assertEqual(len(direct['blasts']), CASE['expected']['blasts'])
                self.assertEqual(direct['live'], CASE['expected']['live'])
