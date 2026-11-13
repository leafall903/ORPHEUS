"""Actual Widow melee target identity and world/owner clocks; no native claim."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run

WIDOWMAKER = 0x02E000000000000A
FIXTURES = Path(os.environ.get(
    "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures/behavior",
))


class WidowmakerMeleeViewClockTests(unittest.TestCase):
    def test_identity_exchange_retains_owner_hit_and_one_world_impulse(self):
        data = json.loads((FIXTURES / "widowmaker-melee-view-clock-v1.json").read_text())
        cfg, expected = data["input"], data["expected"]
        for birth in cfg["owner_births"]:
            with self.subTest(owner_birth=birth):
                run = Run(names=("Shooter", "TargetA", "TargetB"))
                player, a, b = run.players
                stock = tuple(bot.entity for bot in run.match.bots)
                self.assertEqual(len(stock), 15)
                player.commands.last_frame = birth
                run.match.switch_hero(player, heroes()[WIDOWMAKER])
                self.assertEqual(player.body_script.world_frame_anchor, (cfg["world_birth"], birth))
                run.match.collision_world = flat_world(0)
                for owner, pos in ((player, cfg["shooter_position"]),
                                   (a, cfg["near_position"]), (b, cfg["far_position"])):
                    owner.mover.position = list(pos)
                    owner.mover.body = None
                    owner.body_script.create_arrived = True
                fight = run.match.combat
                with patch.object(fight, "_submit_attack", wraps=fight._submit_attack) as submit, \
                        patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as force:
                    for offset in range(1, cfg["end_offset"] + 1):
                        swapped = offset > cfg["swap_after"]
                        a.mover.position[:] = cfg["far_position"] if swapped else cfg["near_position"]
                        b.mover.position[:] = cfg["near_position"] if swapped else cfg["far_position"]
                        tick = cfg["world_birth"] + offset
                        run.match.update(tick * .016, tick)
                        player.take_commands([Command(
                            birth + offset, buttons=0x800 if offset == cfg["attack_offset"] else 0,
                            extra=cfg["view_delay_ms"],
                        )])
                self.assertEqual(submit.call_count, 1)
                source, owner_frame, contact = submit.call_args.args
                self.assertIs(source, player)
                self.assertEqual(owner_frame, birth + cfg["attack_offset"])
                self.assertEqual(contact.view_tick, expected["view_tick"])
                self.assertEqual(contact.impact_tick, expected["impact_tick"])
                targets = {target.owner: target for target in fight._targets()}
                self.assertEqual((fight.vitals_of(targets[a]).total, fight.vitals_of(targets[b]).total),
                                 (expected["health_a"], expected["health_b"]))
                self.assertEqual(force.call_count, expected["force_count"])
                self.assertEqual(force.call_args.args[0].entity, a.body)
                self.assertEqual(list(force.call_args.args[1]), expected["impulse"])
                self.assertEqual(force.call_args.kwargs,
                                 {"impact_tick": expected["impact_tick"], "source_entity": player.body})
                hits = player.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], a.body)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], expected["damage"])
                self.assertEqual(hits[0]["+0x84"], owner_frame)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), stock)
