"""Actual pistol input against exchanged identities, keeping all original NPCs."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, heroes

SOMBRA = 0x02E000000000012E


class SombraViewClockTests(unittest.TestCase):
    def test_actual_pistol_queries_world_history_and_keeps_owner_hit_frame(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/sombra-pistol-view-clock-v1.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        expected = fixture["expected"]
        for birth in fixture["input"]["owner_births"]:
            with self.subTest(owner_birth=birth):
                run = Run(PRACTICE_RANGE, ("Sombra", "TargetA", "TargetB"))
                player, a, b = run.players
                match = run.match
                self.assertEqual(len(match.bots), 15)
                player.commands.reset(last_frame=birth)
                match.switch_hero(player, heroes()[SOMBRA], start_frame=birth)
                player.mover.teleport_grounded((0, 50, 0))
                first, second = (-3, 50, 8), (3, 50, 8)
                for offset in range(1, 42):
                    a.mover.position[:] = first if offset <= 33 else second
                    b.mover.position[:] = second if offset <= 33 else first
                    match.update((1000 + offset) * .016, 1000 + offset)
                    if offset < 41:
                        player.take_commands([Command(birth + offset)])
                fight = match.combat
                targets = {t.owner: t for t in fight._targets()}
                pools = [fight.vitals_of(targets[p]) for p in (a, b)]
                yaw, pitch = aim(fight._eye(player), (-3, 50.45, 8))
                submitted = []
                forward = fight._submit_attack

                def submit(shooter, frame, payload, *, source=player, records=submitted, original=forward):
                    if shooter is source:
                        records.append((frame, payload.view_tick))
                    return original(shooter, frame, payload)

                with patch.object(fight, "_submit_attack", submit):
                    for offset in (41, 42):
                        player.take_commands([Command(birth + offset, yaw=yaw, pitch=pitch,
                                                      buttons=1 if offset == 41 else 0, extra=128)])
                    for tick in range(1042, 1051):
                        match.update(tick * .016, tick)
                hits = player.client.messages(combat.HIT)
                observed = {
                    "submitted": submitted, "health": [p.total for p in pools],
                    "hits": [(h["+0x78"]["+0x0"]["+0x0"], h["+0x84"]) for h in hits],
                    "npc_count": len(match.bots),
                }
                self.assertEqual(observed, {
                    "submitted": [(birth + 41, expected["view_world_tick"])],
                    "health": [expected["target_a_health"], expected["target_b_health"]],
                    "hits": [(a.body, birth + expected["owner_hit_frame_offset"])],
                    "npc_count": expected["npc_count"],
                })
