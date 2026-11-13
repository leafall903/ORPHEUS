"""Actual quick melee must query the viewed identity in the world clock."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import combat, doomfist
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run, aim

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/doomfist-melee-view-clock-v1.json"


class DoomfistMeleeViewClockTests(unittest.TestCase):
    def test_actual_melee_uses_viewed_identity_with_stock_npcs_and_owner_hit_label(self):
        contract = json.loads(FIXTURE.read_text())
        inputs, expected = contract["input"], contract["expected"]
        for birth in inputs["owner_births"]:
            with self.subTest(owner_birth=birth):
                run = Run(names=("Doomfist", "A", "B"))
                player, a, b = run.players
                self.assertEqual(len(run.match.bots), expected["npc_count"])
                player.commands.last_frame = birth
                run.match.switch_hero(player, heroes()[doomfist.HERO])
                origin = tuple(player.mover.position)
                left = tuple(x + d for x, d in zip(origin, inputs["left_offset"], strict=True))
                right = tuple(x + d for x, d in zip(origin, inputs["right_offset"], strict=True))
                yaw, pitch = aim(run.match.combat._eye(player),
                                 (left[0], left[1] + inputs["aim_height"], left[2]))
                fight = run.match.combat
                original, submitted = fight._submit_attack, []

                def submit(shooter, frame, payload, *, source=player, records=submitted, forward=original):
                    if shooter is source and isinstance(payload, combat.MeleeContact):
                        records.append((frame, payload.view_tick))
                    return forward(shooter, frame, payload)

                with patch.object(fight, "_submit_attack", submit):
                    for step in range(1, inputs["frames"] + 1):
                        swapped = step > inputs["swap_after_offset"]
                        a.mover.position[:] = right if swapped else left
                        b.mover.position[:] = left if swapped else right
                        tick = inputs["world_birth"] + step
                        run.match.update(tick * .016, tick)
                        player.take_commands([Command(
                            birth + step, yaw=yaw, pitch=pitch,
                            buttons=inputs["button"] if step == inputs["strike_offset"] else 0,
                            extra=inputs["view_delay_ms"],
                        )])
                frame = birth + inputs["strike_offset"]
                self.assertEqual(submitted, [(frame, expected["view_world_tick"])])
                targets = {t.owner: t for t in fight._targets() if t.owner in (a, b)}
                self.assertEqual(fight.vitals_of(targets[a]).total, expected["A_health"])
                self.assertEqual(fight.vitals_of(targets[b]).total, expected["B_health"])
                hit, = player.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], a.body)
                self.assertEqual(hit["+0x84"], birth + expected["HIT_owner_offset"])
                self.assertEqual(len(run.match.bots), expected["npc_count"])

    def test_mapping_preserves_fractional_history_time(self):
        data = json.loads(FIXTURE.read_text())["fractional_case"]
        script = SimpleNamespace(frame_ms=data["frame_ms"])
        player = SimpleNamespace(body=101, body_script=script)
        fight = SimpleNamespace(knockbacks=SimpleNamespace(body_epochs={
            101: (script, (data["world_birth"], data["owner_birth"]), None),
        }))
        command = Command(data["command_frame"], extra=data["view_delay_ms"])
        self.assertEqual(doomfist.melee_view_world_tick(fight, player, command, data["quanta_us"]),
                         data["expected_world_view"])
