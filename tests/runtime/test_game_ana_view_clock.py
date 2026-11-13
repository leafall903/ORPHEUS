"""Actual Ana input against two target identities exchanged after viewed time."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from ow174.game import ana_projectiles, combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime import test_game_ana as fixture
from tests.runtime.test_game_combat import KANEZAKA_DM, Run, aim


CASE = json.loads((Path(__file__).resolve().parents[2] / "fixtures/behavior/ana-view-clock-v1.json").read_text())


class AnaViewClockTests(unittest.TestCase):
    def test_scoped_rifle_and_melee_select_viewed_identity_across_owner_epochs(self):
        rows = []
        for melee in (False, True):
            for owner_birth in CASE["input"]["owner_births"]:
                with self.subTest(melee=melee, owner_birth=owner_birth):
                    run = Run(KANEZAKA_DM, ('Ana', 'A', 'B'))
                    source, target_a, target_b = run.players
                    npc_ids = tuple(bot.entity for bot in run.match.bots)
                    source.commands.last_frame = owner_birth
                    run.match.switch_hero(source, heroes()[fixture.ANA])
                    self.assertEqual(source.body_script.ana_birth, (1000, owner_birth))
                    start = run.match.tick
                    x, y, z = source.mover.position
                    left = (x - (1.0 if melee else 3), y, z + (1.3 if melee else 8))
                    right = (x + (1.0 if melee else 3), y, z + (1.3 if melee else 8))
                    for offset in range(1, 42):
                        swapped = offset > 33
                        target_a.mover.position[:] = right if swapped else left
                        target_b.mover.position[:] = left if swapped else right
                        tick = start + offset
                        run.match.update(tick * combat.TICK, tick)
                        if offset < 41:
                            source.take_commands([Command(owner_birth + offset, buttons=0 if melee else 2)])
                    fight = run.match.combat
                    targets = {t.owner: t for t in fight._targets()}
                    va, vb = (fight.vitals_of(targets[t]) for t in (target_a, target_b))
                    before = (va.total, vb.total)
                    yaw, pitch = aim(fight._eye(source), (left[0], left[1] + 1.1, left[2]))
                    frame = owner_birth + 41
                    original = fight._submit_attack
                    submitted = []

                    def submit(shooter, at, payload):
                        if shooter is source:
                            submitted.append({'ownerShotFrame': at, 'viewWorldTick': payload.view_tick})
                        return original(shooter, at, payload)

                    with patch.object(fight, '_submit_attack', submit):
                        source.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=0x800 if melee else 3, extra=128)])
                        source.take_commands([Command(frame + 1, yaw=yaw, pitch=pitch, buttons=0 if melee else 2, extra=128)])
                        for offset in range(42, 95):
                            tick = start + offset
                            run.match.update(tick * combat.TICK, tick)
                    hits = source.client.messages(combat.HIT)
                    row = {'kind': 'melee' if melee else 'scoped', 'worldBirth': 1000, 'ownerBirth': owner_birth,
                           'A': target_a.body, 'B': target_b.body, 'beforeHealth': before,
                           'afterHealth': (va.total, vb.total), 'shots': submitted,
                           'hits': [{'target': h['+0x78']['+0x0']['+0x0'], 'ownerFrame': h['+0x84']} for h in hits],
                           'npcCountBefore': len(npc_ids), 'npcCountAfter': len(run.match.bots),
                           'npcIdentitiesRetained': npc_ids == tuple(bot.entity for bot in run.match.bots)}
                    rows.append(row)
                    evidence = os.environ.get('ANA_VIEW_CLOCK_EVIDENCE')
                    if evidence:
                        Path(evidence).write_text(json.dumps({'rows': rows, 'nativeAccepted': False}, indent=2) + '\n', encoding='utf-8')
                    self.assertEqual(submitted, [{'ownerShotFrame': frame, 'viewWorldTick': CASE['expected']['view_world_tick']}])
                    self.assertEqual((va.total, vb.total), (before[0] - CASE['expected']['melee_damage_to_A' if melee else 'scoped_damage_to_A'], before[1]))
                    self.assertTrue(hits)
                    self.assertTrue(all(h['+0x78']['+0x0']['+0x0'] == target_a.body and h['+0x84'] == frame for h in hits))
                    self.assertEqual(tuple(bot.entity for bot in run.match.bots), npc_ids)
                    self.assertEqual(len(npc_ids), CASE["input"]["original_npc_count"])

    def test_view_mapping_preserves_fractional_world_tick(self):
        data = CASE['fractional_case']
        script = SimpleNamespace(ana_birth=(data['world_birth'], data['owner_birth']), frame_ms=data['frame_ms'])
        command = Command(data['command_frame'], extra=data['view_delay_ms'])
        self.assertEqual(ana_projectiles.view_world_tick(script, command, data['quanta_us']),
                         data['expected_world_view'])
