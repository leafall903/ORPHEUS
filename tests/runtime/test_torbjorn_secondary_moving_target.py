"""Actual secondary rays and ammo are unchanged by delayed physical target queries."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_torbjorn import TorbjornProjectileTests

from ow174.game import combat
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/torbjorn-secondary-moving-target-v1.json').read_text())


class TorbjornSecondaryMovingTargetTests(unittest.TestCase):
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
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x1683)
        before = weapon.vars[53].value()
        births = []
        submit = combat.Combat._submit_attack

        def observe(receiver, owner, frame, attack):
            if (isinstance(attack, combat.Rocket)
                    and (attack.source_graph, attack.definition) == (0x1683, 0x04000000000013ED)):
                births.append((attack.definition, attack.position, attack.direction, attack.speed,
                               attack.gravity, attack.source_body, attack.launch_tick, attack.shot is None))
            return submit(receiver, owner, frame, attack)

        start, owner_start = run.frame, shooter.commands.last_frame
        pending = []
        with patch.object(combat.Combat, '_submit_attack', observe):
            for offset in range(1, CASE['input']['sample_frames'] + 1):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick)
                victim.take_commands([Command(tick, right=CASE['input']['target_right'])])
                pending.append(Command(owner_start + offset, buttons=2 if offset == 1 else 0))
                if offset % packet_size == 0:
                    shooter.take_commands(pending)
                    pending = []
        self.assertFalse(pending)
        self.assertEqual(len(births), CASE['expected']['pellets'])
        self.assertEqual(len({row[2] for row in births}), CASE['expected']['pellets'])
        self.assertTrue(all(row[-1] for row in births))  # Authored SHOTS_ONLY has no predicted Shot.
        return {'health': fight.vitals_of(target).total,
                'hits': shooter.client.messages(combat.HIT), 'live': len(fight.rockets),
                'ammo_used': before - weapon.vars[53].value(), 'births': births,
                'recipient_position': tuple(victim.mover.position)}

    def test_delayed_secondary_preserves_ten_rays_ammo_and_full_moving_target_hits(self):
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(packet, separate_epoch)
                                   for packet in CASE['input']['source_packet_sizes']]
                self.assertEqual(direct, delayed)
                self.assertEqual(direct['health'], 200 - CASE['expected']['damage'])
                self.assertEqual(len(direct['hits']), CASE['expected']['hit_notices'])
                self.assertEqual(direct['ammo_used'], CASE['expected']['ammo_used'])
                self.assertEqual(direct['live'], 0)
                self.assertTrue(all(row[-2] is not None for row in direct['births']))
