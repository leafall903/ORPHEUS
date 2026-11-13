"""Distinct historical identities expose Mei melee's owner/world view epoch."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, mei
from ow174.game.commands import Command
from ow174.game.content import heroes

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/mei-melee-view-clock-v1.json').read_text())


class MeiMeleeViewClockTests(unittest.TestCase):
    def exercise(self, owner_birth):
        run = Run(KANEZAKA_DM, ('Mei', 'ViewedA', 'OtherB'))
        source, target_a, target_b = run.players
        npc_count = len(run.match.bots)
        self.assertEqual(npc_count, CONTRACT['input']['npc_count'])
        source.commands.last_frame = owner_birth
        self.assertTrue(run.match.switch_hero(source, heroes()[mei.HERO]))
        self.assertEqual(source.body_script.cryo_birth, (1000, owner_birth))
        x, y, z = source.mover.position
        viewed, other = (x, y, z + 2), (x + 8, y, z + 2)
        for offset in range(1, 42):
            target_a.mover.position[:] = other if offset > 33 else viewed
            target_b.mover.position[:] = viewed if offset > 33 else other
            tick = 1000 + offset
            run.match.update(tick * .016, tick)
            if offset < 41:
                source.take_commands([Command(owner_birth + offset)])
        fight = run.match.combat
        captured = []
        forward = fight._submit_attack

        def submit(shooter, frame, payload):
            if shooter is source and isinstance(payload, combat.MeleeContact):
                captured.append((frame, payload.view_tick))
            return forward(shooter, frame, payload)

        frame = owner_birth + 41
        with patch.object(fight, '_submit_attack', submit):
            source.take_commands([Command(frame, buttons=0x800, extra=128)])
        health = [fight.vitals_of(fight.player_target(p)).total for p in (target_a, target_b)]
        hits = source.client.messages(combat.HIT)
        row = {'owner_birth': owner_birth, 'world_birth': 1000, 'world_query': 1041,
               'view': captured, 'A': target_a.body, 'B': target_b.body, 'health': health,
               'hits': [{'target': h['+0x78']['+0x0']['+0x0'], 'owner_frame': h['+0x84']} for h in hits],
               'fractional_view': mei.view_world_tick(source, owner_birth + 33.5),
               'npc_count_before': npc_count, 'npc_count_after': len(run.match.bots)}
        return row

    def test_actual_melee_rewinds_to_world_identity_and_keeps_owner_hit_label(self):
        rows = [self.exercise(birth) for birth in CONTRACT['input']['owner_births']]
        self.evidence = rows
        for row in rows:
            with self.subTest(owner_birth=row['owner_birth']):
                frame = row['owner_birth'] + 41
                self.assertEqual(row['view'], [(frame, CONTRACT['expected']['view_world_tick'])])
                self.assertEqual(row['health'],
                                 [CONTRACT['expected']['A_health'], CONTRACT['expected']['B_health']])
                self.assertEqual(row['hits'], [{'target': row['A'], 'owner_frame': frame}])
                self.assertEqual(row['npc_count_after'], CONTRACT['expected']['npc_count_after'])
                self.assertEqual(row['fractional_view'],
                                 CONTRACT['expected']['fractional_view_probe']['world_tick'])
