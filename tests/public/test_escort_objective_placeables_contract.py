import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.game import combat, content, heroselect
from ow174.game.match import Match
from tools import extract_game_maps

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class EscortObjectivePlaceablesContractTests(unittest.TestCase):
    def test_escort_adds_server_owned_objectives_health_packs_and_scenery(self):
        contract = json.loads((FIXTURES / "escort-objective-placeables-v1.json").read_text(encoding="utf-8"))
        request, expected = contract["input"], contract["expected"]
        map_guid = int(request["map"], 16)
        escort = int(request["escort_mode"], 16)
        other = int(request["other_mode"], 16)
        catalog = request["catalog"]
        old_catalog = {key: value for key, value in catalog.items()
                       if key not in ("health_packs", "escort_scene_placeables")}
        legacy = {key: value for key, value in old_catalog.items() if key not in ("mirrored", "objectives")}
        for entry, mode, result in (
            (catalog, escort, "escort_placeables"),
            (old_catalog, escort, "old_catalog_escort_placeables"),
            (catalog, other, "other_placeables"),
            (legacy, escort, "legacy_placeables"),
        ):
            with (
                self.subTest(result=result),
                patch.object(content, "_map_entries", return_value={map_guid: entry}),
            ):
                scene = content.game_map(map_guid, mode)
                self.assertIsNotNone(scene)
                self.assertEqual(scene.placeables, tuple(expected[result]))

    def test_map_extractor_lists_unfiltered_server_health_pack_placements(self):
        large = 0x0400000000000FC2
        small = 0x0400000000000FC3
        unrelated = 0x0400000000000001
        placements = [
            {"definition": definition, "client_id": client_id, "identifier": identifier}
            for definition, client_id, identifier in (
                (large, 0, 0), (small, 0, 0), (small, 5, 0), (large, 0, 4), (unrelated, 0, 0),
            )
        ]
        header = {"map": 1, "index": 1, "modes": [0x15]}
        with (
            patch.object(extract_game_maps, "read_map", return_value=(41, placements)),
            patch.object(extract_game_maps, "instance_data", return_value=[[] for _ in placements]),
        ):
            entry = extract_game_maps.build_map(header, Path(), Path(), {}, {})
        self.assertEqual(entry["health_packs"], [{"kind": "large", "placeable": 41},
                                                {"kind": "small", "placeable": 42}])
        self.assertEqual(entry["mirrored"], [41, 42, 44, 45])

    def test_map_extractor_filters_server_scenery_by_model_and_mode_identifier(self):
        model = 0x0400000000000001
        helper = 0x0400000000000002
        allowed, excluded = 7, 8
        placements = [
            {"definition": definition, "client_id": client_id, "identifier": identifier}
            for definition, client_id, identifier in (
                (model, 0, 0), (model, 0, allowed), (model, 0, excluded), (model, 5, 0), (helper, 0, 0),
            )
        ]
        header = {"map": 1, "index": 1, "modes": [extract_game_maps.ESCORT_MODE]}
        with (
            patch.object(extract_game_maps, "read_map", return_value=(41, placements)),
            patch.object(extract_game_maps, "instance_data", return_value=[[] for _ in placements]),
        ):
            entry = extract_game_maps.build_map(
                header, Path(), Path(), {1: {extract_game_maps.MODEL}},
                {extract_game_maps.ESCORT_MODE: [allowed]},
            )
        self.assertEqual(entry["escort_scene_placeables"], [41, 42])

    def test_viewer_finishing_load_after_assembly_gets_the_final_countdown(self):
        contract = json.loads((FIXTURES / "escort-objective-placeables-v1.json").read_text(encoding="utf-8"))
        expected = contract["expected"]["late_viewer"]
        mode = int(contract["input"]["escort_mode"], 16)
        map_guid = int(contract["input"]["map"], 16)
        match = Match.__new__(Match)
        match.id = (1, 2)
        match.game_map = SimpleNamespace(mode_guid=mode, map_guid=map_guid)
        match.scene_host = None
        match.escort = None
        match.controller = heroselect.PVP
        match.send_controller = Mock()
        match.combat = combat.Combat.__new__(combat.Combat)
        match.combat.match = match
        match.combat.voice = Mock()
        match.combat.now = 31.0
        client = Mock()
        player = SimpleNamespace(
            match=match, client=client, steps_done=3, loaded_at=30.0, select_open=True, has_body=True,
        )
        match.players = [player]
        match.assemble_ends = 30.0
        match.assembled = False
        with patch.object(combat, "mode_frame") as encode:
            match._end_assemble(30.0)
            encode.assert_not_called()
            match._spawn_steps(player, client, 31.0)
        encode.assert_called_once_with(
            {expected["countdown_var"]: expected["seconds"]}, mode, map_guid, phase_state=None,
        )
        client.queue_mode_frame.assert_called_once_with(encode.return_value)
        self.assertEqual(player.select_open, expected["select_open_with_hero"])
