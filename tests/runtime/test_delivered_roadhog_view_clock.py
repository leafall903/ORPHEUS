"""Actual Whole Hog input distinguishes historical target identity from current pose."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from ow174.game import combat, hook
from ow174.game.commands import Command
from ow174.game.content import heroes

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/roadhog-whole-hog-view-clock-v1.json"
ROADHOG = 0x02E0000000000040


class RoadhogViewClockTests(unittest.TestCase):
    def scenario(self, owner_birth, *, melee=False):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        inputs = fixture["input"]
        run = Run(KANEZAKA_DM, ("Roadhog", "OnRayAtView", "OffRayAtView"))
        match = run.match
        hog, target_a, target_b = run.players
        npc_before = len(match.bots)
        match.switch_hero(hog, heroes()[ROADHOG], start_frame=owner_birth)
        hog.commands.reset(last_frame=owner_birth)
        hog.body_script.set_ult_charge(2240)
        anchor = tuple(hog.mover.position)
        on_offset = fixture["melee_case"]["on_ray_offset"] if melee else inputs["target_on_ray_offset"]
        on_ray = tuple(a + b for a, b in zip(anchor, on_offset, strict=True))
        off_ray = tuple(a + b for a, b in zip(anchor, inputs["target_off_ray_offset"], strict=True))
        fight = match.combat
        queries = []
        forward = fight._submit_attack

        def submit(shooter, at, payload):
            if shooter is hog and isinstance(payload, (combat.RangedContact, combat.MeleeContact)):
                queries.append((at, payload.view_tick))
            return forward(shooter, at, payload)

        with patch.object(fight, "_submit_attack", submit):
            for offset in range(1, inputs["first_volley_offset"] + 1):
                swapped = offset > inputs["swap_after_offset"]
                target_a.mover.position[:] = off_ray if swapped else on_ray
                target_b.mover.position[:] = on_ray if swapped else off_ray
                tick = inputs["world_birth"] + offset
                match.update(tick * combat.TICK, tick)
                buttons = (fixture["melee_case"]["button"]
                           if offset == fixture["melee_case"]["fire_offset"] else 0) if melee else (
                    32 if offset == inputs["ultimate_button_offset"] else 0)
                hog.take_commands([Command(owner_birth + offset, buttons=buttons,
                    extra=inputs["view_delay_ms"])])
        targets = {target.owner: target for target in fight._targets()}
        hits = hog.client.messages(combat.HIT)
        return {"attack": "melee" if melee else "whole_hog",
                "owner_birth": owner_birth, "world_birth": inputs["world_birth"],
                "npc_before": npc_before, "npc_after": len(match.bots),
                "target_a": target_a.body, "target_b": target_b.body,
                "health_a": fight.vitals_of(targets[target_a]).total,
                "health_b": fight.vitals_of(targets[target_b]).total,
                "queries": sorted(set(queries)),
                "hit_targets": sorted({hit["+0x78"]["+0x0"]["+0x0"] for hit in hits}),
                "hit_owner_frames": sorted({hit["+0x84"] for hit in hits})}

    def test_original_whole_hog_hits_viewed_identity_and_keeps_owner_hit_frame(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        expected = fixture["expected"]
        for origin in fixture["input"]["owner_births"]:
            with self.subTest(owner_birth=origin):
                row = self.scenario(origin)
                self.assertEqual(row["npc_before"], expected["original_npc_count"])
                self.assertEqual(row["npc_after"], expected["original_npc_count"])
                self.assertEqual(row["queries"], [(origin + expected["owner_hit_frame_offset"],
                                                  expected["view_world_tick"])])
                self.assertLess(row["health_a"], 200)
                self.assertEqual(row["health_b"], expected["unselected_target_health"])
                self.assertEqual(row["hit_targets"], [row["target_a"]])
                self.assertEqual(row["hit_owner_frames"], [origin + expected["owner_hit_frame_offset"]])

    def test_view_mapping_preserves_fractional_world_tick(self):
        script = SimpleNamespace(roadhog_birth=(1000, 9000), frame_ms=16)
        self.assertEqual(hook.view_world_tick(script, Command(9041, extra=125), 16000), 1033.1875)

    def test_actual_quick_melee_queries_the_same_viewed_identity(self):
        row = self.scenario(9000, melee=True)
        self.assertEqual(row["queries"], [(9032, 1024)])
        self.assertEqual((row["npc_before"], row["npc_after"]), (15, 15))
        self.assertEqual((row["health_a"], row["health_b"]), (170, 200))
        self.assertEqual(row["hit_targets"], [row["target_a"]])
        self.assertEqual(row["hit_owner_frames"], [9032])
