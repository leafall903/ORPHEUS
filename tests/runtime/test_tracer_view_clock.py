"""Actual Tracer target identities distinguish world history from owner frames."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, tracer_pistol
from ow174.game.commands import Command
from ow174.game.content import heroes

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/tracer-view-clock-v1.json'


class TracerViewClockTests(unittest.TestCase):
    def test_actual_pistol_identity_and_owner_hit_frame(self):
        data = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for birth in data['input']['owner_births']:
            with self.subTest(owner_birth=birth):
                self.identity_case(data, 'pistol', birth)

    def test_actual_melee_identity_uses_the_same_world_query(self):
        self.identity_case(json.loads(FIXTURE.read_text(encoding='utf-8')), 'melee', 9000)

    def identity_case(self, data, action, birth):
        cfg, expected = data['input'], data['expected']
        run = Run(KANEZAKA_DM, ('Tracer', 'A', 'B'))
        player, target_a, target_b = run.players
        count = len(run.match.bots)
        self.assertEqual(count, 15)
        player.commands.last_frame = birth
        run.match.switch_hero(player, heroes()[tracer_pistol.HERO])
        self.assertEqual(player.body_script.world_frame_anchor, (cfg['world_birth'], birth))
        start = run.match.tick
        origin = tuple(player.mover.position)
        a = tuple(p + d for p, d in zip(origin, cfg[action + '_a_offset'], strict=True))
        b = tuple(p + d for p, d in zip(origin, cfg[action + '_b_offset'], strict=True))
        for offset in range(1, cfg['shot_offset'] + 1):
            swapped = offset > cfg['swap_after_offset']
            target_a.mover.position[:] = b if swapped else a
            target_b.mover.position[:] = a if swapped else b
            tick = start + offset
            run.match.update(tick * combat.TICK, tick)
            if offset < cfg['shot_offset']:
                player.take_commands([Command(birth + offset)])
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        va, vb = fight.vitals_of(targets[target_a]), fight.vitals_of(targets[target_b])
        yaw, pitch = aim(fight._eye(player), (a[0], a[1] + cfg['aim_height'], a[2]))
        frame = birth + cfg['shot_offset']
        original, submitted = fight._submit_attack, []

        def submit(shooter, at, payload):
            if shooter is player:
                submitted.append((at, payload.view_tick))
            return original(shooter, at, payload)

        with patch.object(fight, '_submit_attack', submit):
            player.take_commands([Command(frame, yaw=yaw, pitch=pitch,
                                          buttons=1 if action == 'pistol' else 0x800,
                                          extra=cfg['view_delay_ms'])])
            player.take_commands([Command(frame + 1, yaw=yaw, pitch=pitch,
                                          extra=cfg['view_delay_ms'])])
            for offset in range(cfg['shot_offset'] + 1, cfg['shot_offset'] + 10):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick)
        hits = player.client.messages(combat.HIT)
        print(json.dumps({'action': action, 'ownerBirth': birth, 'worldBirth': start,
                          'A': target_a.body, 'B': target_b.body, 'submitted': submitted,
                          'health': [va.total, vb.total], 'npcCount': len(run.match.bots),
                          'hits': [{'target': h['+0x78']['+0x0']['+0x0'],
                                    'ownerFrame': h['+0x84']} for h in hits]}), flush=True)
        n = 2 if action == 'pistol' else 1
        self.assertEqual(submitted, [(frame, expected['view_world_tick'])] * n)
        self.assertEqual(va.total, expected[action + '_a_health'])
        self.assertEqual(vb.total, expected['b_health'])
        self.assertEqual(len(hits), n)
        self.assertTrue(all(h['+0x78']['+0x0']['+0x0'] == target_a.body for h in hits))
        self.assertTrue(all(h['+0x84'] == frame for h in hits))
        self.assertEqual(len(run.match.bots), count)

    def test_view_query_preserves_fractional_world_ticks(self):
        cfg = json.loads(FIXTURE.read_text(encoding='utf-8'))['fractional_case']
        script = SimpleNamespace(world_frame_anchor=(cfg['world_birth'], cfg['owner_birth']),
                                 frame_ms=cfg['frame_ms'])
        command = Command(cfg['command_frame'], extra=cfg['view_delay_ms'])
        self.assertEqual(tracer_pistol.view_world_tick(script, command, cfg['quanta_us']),
                         cfg['expected_world_view'])
