"""Actual Whip launch uses original graph433 rather than an unrelated primary click."""
import json
import unittest
from pathlib import Path

import test_game_brigitte as fixtures

from ow174.game import combat
from ow174.game.commands import Command


class WhipCapturedDirectionTests(unittest.TestCase):
    def test_primary_subframe_aim_cannot_replace_authored_whip_facing(self):
        data = json.loads((Path(__file__).resolve().parents[1] /
                           "fixtures/behavior/brigitte-whip-captured-direction-v1.json").read_text())
        case = fixtures.BrigitteTests()
        run, brig = case.fixture(names=("Brigitte", "GraphFacingTarget", "PrimaryClickTarget"))
        a, b = run.players[1:]
        x, y, z = brig.mover.position
        for owner, position in ((a, (x, y, z + data["distance"])), (b, (x + data["distance"], y, z))):
            owner.mover.position, owner.mover.body = list(position), None
        for offset in range(1, data["release_offset"]):
            run.step({brig: (data["body_yaw"], 0, 8 if offset == 1 else 0)})
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        brig.take_commands([Command(run.frame, yaw=data["body_yaw"], buttons=1 | 0x10000,
                                    subframe=data["click_subframe"],
                                    click_yaw=data["primary_click_yaw"], click_pitch=0)])
        fight = run.match.combat
        [flight] = fight.brigitte.whips
        captured = flight.instance.vars[433].value()
        self.assertEqual(list(captured), data["captured_direction"])
        self.assertEqual(flight.direction, tuple(captured))
        for _ in range(20):
            run.step({})
        health = [fight.vitals_of(case.target(run, owner)).total for owner in (a, b)]
        self.assertEqual(health, data["expected_health"])
        [hit] = brig.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], a.body)
        self.assertEqual(hit["+0x78"]["+0x4"], 70)
