"""Flail's own history query preserves target identity across owner epochs."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat
from ow174.game.brigitte import HERO
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes


class BrigitteViewClockTests(unittest.TestCase):
    def test_real_flail_input_rewinds_world_identity_and_keeps_owner_hit_frame(self):
        data = json.loads(
            (
                Path(__file__).resolve().parents[1] / "fixtures/behavior/brigitte-view-clock-v1.json"
            ).read_text()
        )
        for birth in data["owner_births"]:
            with self.subTest(owner_birth=birth):
                run = Run(names=("Brigitte", "TargetA", "TargetB"))
                brig, a, b = run.players
                npcs = tuple(bot.entity for bot in run.match.bots)
                self.assertEqual(len(npcs), 15)
                brig.commands.last_frame = birth
                run.match.switch_hero(brig, heroes()[HERO])
                self.assertEqual(brig.body_script.brigitte_birth, (data["world_birth"], birth))
                run.match.collision_world = flat_world(0)
                pos_a, pos_b = data["target_a_position"], data["target_b_position"]
                for owner, position in ((brig, (1000, 0, 1000)), (a, pos_a), (b, pos_b)):
                    owner.mover.position = list(position)
                    owner.mover.body = None
                    owner.body_script.create_arrived = True
                fight = run.match.combat
                queries = []
                original = fight.seen

                def seen(target, tick, original=original, queries=queries):
                    queries.append(tick)
                    return original(target, tick)

                with patch.object(fight, "seen", seen):
                    for offset in range(1, data["end_offset"] + 1):
                        swapped = offset > data["swap_after"]
                        a.mover.position[:] = pos_b if swapped else pos_a
                        b.mover.position[:] = pos_a if swapped else pos_b
                        tick = data["world_birth"] + offset
                        run.match.update(tick * 0.016, tick)
                        brig.take_commands(
                            [
                                Command(
                                    birth + offset,
                                    buttons=1 if offset == data["press_offset"] else 0,
                                    extra=data["view_delay_ms"],
                                )
                            ]
                        )
                targets = {target.owner: target for target in fight._targets()}
                health = [fight.vitals_of(targets[owner]).total for owner in (a, b)]
                hits = brig.client.messages(combat.HIT)
                print(
                    json.dumps(
                        {
                            "ownerBirth": birth,
                            "queries": sorted(set(queries)),
                            "health": health,
                            "hits": [[h["+0x78"]["+0x0"]["+0x0"], h["+0x84"]] for h in hits],
                        }
                    )
                )
                self.assertEqual(health, data["expected_health"])
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], a.body)
                self.assertEqual(hits[0]["+0x84"], birth + 54)
                self.assertTrue(queries)
                self.assertLess(max(queries), 1100)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)
