"""Actual ally movement and accepted29AD flight under delayed source input."""

import json
import unittest
from pathlib import Path

import test_baptiste_projectile_delivery as fixture

from ow174.game import combat
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2] / "fixtures/behavior"
                   / "baptiste-heal-moving-target-v1.json").read_text(encoding="utf-8"))


class BaptisteHealTargetHistoryTests(unittest.TestCase):
    def replay(self, packet_size):
        run, owner, ally, enemy, fight, targets = fixture.BaptisteProjectileDeliveryTests().arena(
            friendly_target=True)
        fight.vitals_of(targets[ally]).damage(CASE["input"]["initial_damage"])
        first, pending = run.frame, []
        for offset in range(1, CASE["input"]["frames"] + 1):
            tick = first + offset
            run.match.update(tick * combat.TICK, tick)
            ally.take_commands([Command(tick, right=CASE["input"]["target_right"])])
            pending.append(Command(tick, buttons=2 if offset == 1 else 0))
            if offset % packet_size == 0:
                owner.take_commands(pending)
                pending = []
        return {"ally_health": fight.vitals_of(targets[ally]).total,
                "enemy_health": fight.vitals_of(targets[enemy]).total,
                "hits": owner.client.messages(combat.HIT), "live": len(fight.rockets),
                "target_pose": tuple(ally.mover.position)}

    def test_actual_moving_ally_keeps_heal_and_owner_notices_under_packet_delay(self):
        normal, delayed = [self.replay(size) for size in CASE["input"]["source_packet_sizes"]]
        self.assertEqual(normal, delayed)
        self.assertEqual(normal["ally_health"], CASE["expected"]["ally_health"])
        self.assertEqual(normal["enemy_health"], CASE["expected"]["enemy_health"])
        self.assertEqual(normal["live"], 0)
        self.assertEqual(normal["hits"], [])  # This healing path does not synthesize damage HIT.
