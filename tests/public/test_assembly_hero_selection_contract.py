import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import heroselect
from ow174.game.statescript import Asset, Bool

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES") or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class AssemblyHeroSelectionContractTests(unittest.TestCase):
    def setUp(self):
        path = FIXTURES / "assembly-hero-selection-v1.json"
        self.contract = json.loads(path.read_text(encoding="utf-8"))

    def test_change_hero_is_distinct_from_invalid_parameters(self):
        for step, parameters in self.contract["input"]["parameters"].items():
            with self.subTest(step=step):
                guid = self.contract["expected"]["parsed_guid"][step]
                expected = int(guid, 16) if guid is not None else None
                self.assertEqual(heroselect.picked_hero(bytes.fromhex(parameters)), expected)

    def test_controller_frame_shows_browsing_or_confirmed_state(self):
        expected = self.contract["expected"]
        variables = expected["wire_variables"]
        for step in ("first_pick", "change_hero", "second_pick", "assembled"):
            with self.subTest(step=step), patch.object(heroselect, "owner_full_frame") as encode:
                state = expected[step]
                heroselect.controller_frame(
                    heroselect.PVP, 1, state["open"], skins=state["open"],
                    hero=int(state["current_hero"], 16), pick_confirmed=state["confirmed"],
                )
                self.assertEqual(encode.call_args.args[2], {
                    variables["open"]: Bool(state["open"]),
                    variables["shown"]: Bool(state["open"]),
                    variables["confirmed"]: Bool(state["confirmed"]),
                    variables["current_hero"]: Asset(int(state["current_hero"], 16)),
                    variables["picked_hero"]: Asset(int(state["picked_hero"], 16)),
                })
