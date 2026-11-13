"""Whip Shot uses its authored launch clock across received command batches."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_brigitte as fixtures

from ow174.game import combat
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.script import runtime


class WhipBatchClockTests(unittest.TestCase):
    def test_whip_damage_world_tick_and_owner_hit_remain_distinct(self):
        data = json.loads((Path(__file__).resolve().parents[1] /
                           "fixtures/behavior/brigitte-whip-owner-hit-v1.json").read_text())
        case = fixtures.BrigitteTests()
        run, brig = case.fixture()
        enemy = run.players[1]
        brig.commands.last_frame = data["owner_birth"]
        run.match.switch_hero(brig, brig.hero)
        x, y, z = brig.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + data["distance"]], None
        fight = run.match.combat
        damage_frames = []
        original = fight._damage

        def damage(shooter, target, amount, critical, loadout, frame, **kwargs):
            damage_frames.append((frame, kwargs["world_tick"]))
            return original(shooter, target, amount, critical, loadout, frame, **kwargs)

        with patch.object(fight, "_damage", damage):
            for offset in range(1, 61):
                tick = data["world_birth"] + offset
                run.match.update(tick * .016, tick)
                brig.take_commands([Command(data["owner_birth"] + offset, buttons=8 if offset == 1 else 0)])
                if damage_frames:
                    break
        self.assertEqual(damage_frames, [(data["expected_owner_hit"], data["expected_world_contact"])])
        target = case.target(run, enemy)
        self.assertEqual(fight.vitals_of(target).total, 200 - data["damage"])
        [hit] = brig.client.messages(combat.HIT)
        self.assertEqual(hit["+0x84"], data["expected_owner_hit"])
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], enemy.body)
        self.assertEqual(hit["+0x78"]["+0x4"], data["damage"])

    def test_birth_epoch_is_causal_snapshot_storage_without_live_evaluation(self):
        _run, brig = fixtures.BrigitteTests().fixture()
        script = brig.body_script
        birth = script.brigitte_birth
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no live getter")):
            baseline = snapshot_digest(export_body_vm(script))
            script.brigitte_birth = (birth[0] + 1, birth[1])
            shifted = snapshot_digest(export_body_vm(script))
            script.brigitte_birth = birth
            restored = snapshot_digest(export_body_vm(script))
        self.assertNotEqual(baseline, shifted)
        self.assertEqual(baseline, restored)
        self.assertIsNone(script.world_frame_anchor)

    def scenario(self, batch, *, hit):
        case = fixtures.BrigitteTests()
        run, brig = case.fixture()
        fight = run.match.combat
        enemy = run.players[1]
        x, y, z = brig.mover.position
        enemy.mover.position, enemy.mover.body = [x if hit else x + 50, y, z + 10], None
        for _ in range(64):
            run.step({})
        birth = brig.body_script.brigitte_birth
        commands = [Command(frame, buttons=8 if frame == 1081 else 0)
                    for frame in range(1065, 1097)]
        for start in range(0, len(commands), batch):
            packet = commands[start:start + batch]
            frame = packet[-1].frame
            run.match.update(frame * .016, frame)
            brig.take_commands(packet)
        self.assertEqual(brig.body_script.brigitte_birth, birth)
        self.assertIsNone(brig.body_script.world_frame_anchor)
        [flight] = fight.brigitte.whips
        self.assertAlmostEqual(flight.position[2] - z, 3.2, places=6)
        self.assertAlmostEqual(flight.remaining, 16.8, places=6)
        self.assertEqual(fight.vitals_of(case.target(run, enemy)).total, 200)
        self.assertFalse(brig.client.messages(combat.HIT))
        for start in range(1097, 1129, batch):
            packet = [Command(frame) for frame in range(start, min(1129, start + batch))]
            frame = packet[-1].frame
            run.match.update(frame * .016, frame)
            brig.take_commands(packet)
        self.assertFalse(fight.brigitte.whips)
        hits = brig.client.messages(combat.HIT)
        self.assertEqual(fight.vitals_of(case.target(run, enemy)).total, 130 if hit else 200)
        self.assertEqual(len(hits), int(hit))
        if hit:
            self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], enemy.body)
            self.assertEqual(hits[0]["+0x78"]["+0x4"], 70)
        return [(h["+0x78"]["+0x4"], h["+0x84"]) for h in hits]

    def test_same_whip_event_keeps_flight_hit_feedback_and_retirement_across_batches(self):
        for hit in (False, True):
            results = []
            for batch in (1, 8, 32):
                with self.subTest(hit=hit, batch=batch):
                    results.append(self.scenario(batch, hit=hit))
            self.assertEqual(results, [results[0]] * 3)
