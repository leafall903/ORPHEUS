"""Actual0043 contact distinguishes historical identity from the current pose."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat, zarya
from ow174.game.commands import Command
from ow174.game.content import heroes


class ZaryaMeleeViewClockTests(unittest.TestCase):
    def test_melee_selects_viewed_identity_and_keeps_owner_hit_frame(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/zarya-melee-view-clock-v1.json"
        fixture = json.loads(path.read_text())
        for owner_birth in fixture["owner_births"]:
            with self.subTest(owner_birth=owner_birth):
                run = Run(names=("Zarya", "A", "B"))
                match, fight = run.match, run.match.combat
                caster, a, b = run.players
                count = len(match.bots)
                self.assertEqual(count, fixture["expected"]["npc_count"])
                match.switch_hero(caster, heroes()[zarya.HERO], start_frame=owner_birth)
                caster.commands.reset(last_frame=owner_birth)
                caster.team, a.team, b.team = 0, 1, 1
                caster.mover.position[:] = [0, 1, 0]
                for player in run.players:
                    player.mover.body = None
                submitted = []
                original = fight._submit_attack

                def capture(shooter, frame, payload, *, source=caster, rows=submitted, forward=original):
                    if shooter is source and isinstance(payload, combat.MeleeContact):
                        rows.append((frame, payload.view_tick))
                    return forward(shooter, frame, payload)

                with patch.object(fight, "_submit_attack", capture):
                    for offset in range(1, fixture["attack_offset"] + 10):
                        swapped = offset > fixture["swap_after_offset"]
                        near, far = fixture["near_position"], fixture["far_position"]
                        a.mover.position[:] = far if swapped else near
                        b.mover.position[:] = near if swapped else far
                        world = fixture["world_birth"] + offset
                        match.update(world * combat.TICK, world)
                        caster.take_commands([Command(
                            owner_birth + offset,
                            buttons=0x800 if offset == fixture["attack_offset"] else 0,
                            extra=fixture["view_delay_ms"],
                        )])
                        a.take_commands([Command(world)])
                        b.take_commands([Command(world)])
                frame = owner_birth + fixture["attack_offset"]
                self.assertEqual(submitted, [(frame, fixture["expected"]["view_world_tick"])])
                self.assertEqual(fight.vitals[a.body].total, fixture["expected"]["health_A"])
                self.assertEqual(fight.vitals[b.body].total, fixture["expected"]["health_B"])
                hits = caster.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], a.body)
                self.assertEqual(hits[0]["+0x84"], frame)
                self.assertEqual(len(match.bots), count)
