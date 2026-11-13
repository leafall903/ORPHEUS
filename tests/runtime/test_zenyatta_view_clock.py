"""Distinct melee targets prove viewed-world identity; native acceptance pending."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import combat, zenyatta
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run


class ZenyattaViewClockTests(unittest.TestCase):
    def test_melee_rewinds_target_identity_in_world_domain_and_keeps_owner_hit_frame(self):
        for owner_birth in (1000, 9000):
            with self.subTest(owner_birth=owner_birth):
                run = Run(names=("Zen", "OriginallyInReach", "OriginallyOutside"))
                source, first, second = run.players
                bots = tuple(bot.entity for bot in run.match.bots)
                self.assertEqual(len(bots), 15)
                source.commands.last_frame = owner_birth
                run.match.switch_hero(source, heroes()[zenyatta.HERO])
                self.assertEqual(source.body_script.world_frame_anchor, (1000, owner_birth))
                x, y, z = source.mover.position
                near, far = [x, y, z + 1.5], [x + 5, y, z + 1.5]
                for offset in range(1, 42):
                    first.mover.position[:] = near if offset <= 33 else far
                    second.mover.position[:] = far if offset <= 33 else near
                    run.frame = 1000 + offset
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if offset < 41:
                        source.take_commands([Command(owner_birth + offset)])
                fight = run.match.combat
                targets = {target.owner: target for target in fight._targets()}
                first_hp = fight.vitals_of(targets[first])
                second_hp = fight.vitals_of(targets[second])
                submitted = []
                forward = fight._submit_attack

                def capture(shooter, frame, payload, source=source, submitted=submitted, forward=forward):
                    if shooter is source:
                        submitted.append((frame, payload.view_tick))
                    return forward(shooter, frame, payload)

                owner_hit_frame = owner_birth + 41
                with patch.object(fight, "_submit_attack", side_effect=capture):
                    source.take_commands([Command(owner_hit_frame, buttons=0x800, extra=128)])
                hits = source.client.messages(combat.HIT)
                actual = (
                    submitted, first_hp.total, second_hp.total,
                    [(hit["+0x78"]["+0x0"]["+0x0"], hit["+0x84"]) for hit in hits],
                )
                expected = (
                    [(owner_hit_frame, 1033)], 170, 200, [(first.body, owner_hit_frame)],
                )
                self.assertEqual(actual, expected)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), bots)

    def test_view_clock_retains_fractional_world_time(self):
        script = SimpleNamespace(world_frame_anchor=(1000, 9000), frame_ms=15)
        command = Command(9041, extra=8)
        self.assertEqual(zenyatta.view_world_tick(script, command, 15000), 1037.9375)
