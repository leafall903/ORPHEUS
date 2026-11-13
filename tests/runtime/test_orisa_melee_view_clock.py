"""Actual Orisa melee resolves viewed target identity across owner epochs."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat, orisa_clock
from ow174.game.commands import Command
from ow174.game.content import find_hero

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/orisa-melee-view-clock-v1.json"


class OrisaMeleeViewClockTests(unittest.TestCase):
    def test_actual_melee_keeps_viewed_identity_and_owner_hit_with_all_npcs(self):
        case = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for birth in case["owner_births"]:
            with self.subTest(owner_birth=birth):
                run = Run(names=("Orisa", "A", "B"))
                caster, a, b = run.players
                self.assertEqual(len(run.match.bots), case["expected_npcs"])
                caster.commands.last_frame = birth
                run.match.switch_hero(caster, find_hero("Orisa"))
                self.assertEqual(caster.body_script.orisa_birth, (case["world_birth"], birth))
                start = run.match.tick
                origin = tuple(caster.mover.position)
                front = tuple(x + d for x, d in zip(origin, case["front_offset"], strict=True))
                other = tuple(x + d for x, d in zip(origin, case["off_axis_offset"], strict=True))
                for offset in range(1, case["attack_offset"] + 1):
                    a.mover.position[:] = other if offset > case["exchange_after_offset"] else front
                    b.mover.position[:] = front if offset > case["exchange_after_offset"] else other
                    run.match.update((start + offset) * .016, start + offset)
                    if offset < case["attack_offset"]:
                        caster.take_commands([Command(birth + offset)])
                fight = run.match.combat
                av, bv = (fight.vitals_of(fight.player_target(player)) for player in (a, b))
                yaw, pitch = aim(fight._eye(caster), (front[0], front[1] + 1, front[2]))
                submitted = []
                original = fight._submit_attack

                def submit(player, frame, payload):
                    if player is caster:
                        submitted.append((frame, payload.view_tick))
                    return original(player, frame, payload)

                frame = birth + case["attack_offset"]
                with patch.object(fight, "_submit_attack", submit):
                    caster.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=0x800,
                                                  extra=case["view_delay_ms"])])
                    caster.take_commands([Command(frame + 1, yaw=yaw, pitch=pitch,
                                                  extra=case["view_delay_ms"])])
                    for offset in range(case["attack_offset"] + 1, case["attack_offset"] + 6):
                        run.match.update((start + offset) * .016, start + offset)
                self.assertEqual(submitted, [(frame, case["expected_view_world_tick"])])
                self.assertEqual([av.total, bv.total], case["expected_health"])
                hits = caster.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], a.body)
                self.assertEqual(hits[0]["+0x84"], frame)
                self.assertEqual(len(run.match.bots), case["expected_npcs"])

    def test_view_query_keeps_fractional_world_ticks(self):
        script = SimpleNamespace(orisa_birth=(1000, 9000), frame_ms=16)
        self.assertEqual(orisa_clock.view_world_tick(script, Command(9041, extra=125), 16000), 1033.1875)
