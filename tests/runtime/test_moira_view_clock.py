"""World target history must not be indexed by a Moira owner command label."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat, moira
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class MoiraViewClockTests(unittest.TestCase):
    observations: ClassVar[list] = []
    contract = json.loads((Path(__file__).resolve().parents[2]
                           / "fixtures/behavior/moira-view-clock-174-v1.json").read_text())

    def exercise(self, action, births):
        for owner_birth in births:
            with self.subTest(action=action, owner_birth=owner_birth):
                run = Run(names=('Moira', 'EarlierA', 'EarlierB'))
                match = run.match
                owner, a, b = run.players
                original_npcs = tuple(bot.entity for bot in match.bots)
                self.assertEqual(len(original_npcs), 15)
                owner.commands.reset(last_frame=owner_birth)
                self.assertTrue(match.switch_hero(owner, heroes()[moira.MOIRA], start_frame=owner_birth))
                self.assertEqual(owner.body_script.moira_birth, (1000, owner_birth))
                start = match.tick
                x, y, z = owner.mover.position
                slot_a, slot_b = ((x - .6, y, z + 1.5), (x + 4, y, z + 1.5)) if action == 'melee' else (
                    (x - 3, y, z + 8), (x + 3, y, z + 8))
                if action == 'coalescence':
                    owner.body_script.component.set(ULT_CHARGE,
                        owner.body_script.component.entity_var(ULT_COST).value())
                for offset in range(1, 42):
                    a.mover.position[:] = slot_b if offset > 33 else slot_a
                    b.mover.position[:] = slot_a if offset > 33 else slot_b
                    match.update((start + offset) * .016, start + offset)
                    if offset < 41:
                        owner.take_commands([Command(
                            owner_birth + offset,
                            buttons=32 if action == 'coalescence' and offset == 1 else 0)])
                fight = match.combat
                targets = {t.owner: t for t in fight._targets()}
                health = [fight.vitals_of(targets[p]) for p in (a, b)]
                yaw, pitch = aim(fight._eye(owner), (slot_a[0], slot_a[1] + 1.1, slot_a[2]))
                queries = []
                original = fight.seen

                def seen(target, tick, *, actors=(a, b), records=queries, forward=original):
                    if target.owner in actors:
                        records.append(tick)
                    return forward(target, tick)

                frame = owner_birth + 41
                with patch.object(fight, 'seen', side_effect=seen):
                    owner.take_commands([Command(
                        frame, yaw=yaw, pitch=pitch,
                        buttons={'grasp': 2, 'coalescence': 0, 'melee': 0x800}[action], extra=128)])
                hits = owner.client.messages(combat.HIT)
                row = {'action': action, 'owner_birth': owner_birth, 'world_birth': 1000,
                       'npc_count': len(match.bots),
                       'A': a.body, 'B': b.body, 'health': [v.total for v in health],
                       'queries': sorted(set(queries)),
                       'hits': [(h['+0x78']['+0x0']['+0x0'], h['+0x84']) for h in hits]}
                self.observations.append(row)
                self.assertEqual(tuple(bot.entity for bot in match.bots), original_npcs)
                self.assertTrue(queries)
                self.assertEqual(set(queries), {self.contract["expected"]["query_world_tick"]})
                self.assertAlmostEqual(health[0].total, self.contract["expected"]["A_health"][action])
                self.assertEqual(health[1].total, 200)
                self.assertTrue(hits)
                self.assertTrue(all(h['+0x78']['+0x0']['+0x0'] == a.body for h in hits))
                self.assertTrue(all(h['+0x84'] == frame for h in hits))

    def test_actual_grasp_keeps_viewed_target_identity_with_separate_owner_epoch(self):
        self.exercise('grasp', (1000, 9000))

    def test_actual_coalescence_keeps_viewed_target_identity(self):
        self.exercise('coalescence', (9000,))

    def test_actual_melee_keeps_viewed_target_identity(self):
        self.exercise('melee', (9000,))

    def test_view_mapping_preserves_fractional_world_time(self):
        data = self.contract['fractional']
        script = SimpleNamespace(moira_birth=(data['world_birth'], data['owner_birth']),
                                 frame_ms=data['frame_ms'])
        command = Command(data['command_frame'], extra=data['view_delay_ms'])
        self.assertEqual(moira.view_world_tick(script, command, data['quanta_us']),
                         data['expected_world_view'])
