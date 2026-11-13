"""Actual moving target inputs discriminate physical-tick collision from receipt poses."""

import unittest

import test_orisa_combat as fixture

from ow174.game import combat
from ow174.game.commands import Command


class OrisaPrimaryMovingTargetTests(unittest.TestCase):
    def replay(self, packet_size, separate_epoch):
        run, shooter, victim = fixture.OrisaCombatTests().arena()
        target = fixture.OrisaCombatTests().target(run, victim)
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
        for offset in range(1, 97):
            tick = world_start + offset
            run.match.update(tick * combat.TICK, tick)
            victim.take_commands([Command(tick, right=127)])
            pending.append(Command(owner_start + offset, buttons=1 if offset == 1 else 0))
            if offset % packet_size == 0:
                shooter.take_commands(pending)
                pending = []
        fight = run.match.combat
        return {'health': fight.vitals_of(target).total,
                'hits': shooter.client.messages(combat.HIT), 'live': len(fight.rockets)}

    def test_packet_delay_keeps_moving_target_damage_and_full_owner_hit_payload(self):
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(size, separate_epoch) for size in (1, 32)]
                self.assertEqual(direct, delayed)
                self.assertEqual(direct['health'], 178)
                self.assertEqual(len(direct['hits']), 1)
                self.assertEqual(direct['live'], 0)
