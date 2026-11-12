"""Generate missing client-derived inputs while retaining authorized source files."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game.casc import RECORD_CATALOG_BUILD, RECORD_CATALOG_SCHEMA
from ow174.game.collision import chunk_guid
from ow174.game.content import PRACTICE_RANGE
from ow174.paths import CLIENT_CATALOGS, LOBBY_RUNTIME_DATA, MATCH_RUNTIME_DATA
from tools.extract_arcade_cards import ASSET_FLAGS
from tools.prepare_map_data import write_practice_props
from tools.prepare_server_runtime import prepare


class RuntimeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = self.folder / "source"
        self.data = self.source / "data"
        self.data.mkdir(parents=True)
        for name in LOBBY_RUNTIME_DATA + MATCH_RUNTIME_DATA:
            (self.data / name).write_text("prepared fixture", encoding="utf-8")
        descriptors = self.source / "ow174" / "bnet" / "descriptors.pb"
        descriptors.parent.mkdir(parents=True)
        descriptors.write_bytes(b"synthetic descriptors")
        self.game = self.folder / "game"
        (self.game / "data" / "casc" / "data").mkdir(parents=True)
        (self.game / ".build.info").write_text(
            "Product!STRING:0|Version!STRING:0|Active!DEC:1\npro|1.74.0.0.104319|1\n",
            encoding="utf-8",
        )
        self.tool = self.folder / "DataTool.exe"
        self.tool.touch()
        self.destination = self.folder / "runtime"

    def test_generates_missing_data_and_collision_preserving_prepared_files(self):
        missing = (
            "extracted_heroes.json", "game_heroes_174.json", "arcade_cards_174.json",
            "game_maps_174.json", "collision_keys_174.json", "ai_spawners_174.json", "game_queues_174.json",
        )
        for name in missing:
            (self.data / name).unlink()
        scene = PRACTICE_RANGE
        map_key = f"0x{scene.map_guid:016X}"
        chunk_key = f"0x{chunk_guid(scene.map_guid):016X}"
        record = {"ckey": "12" * 16, "ekey": "34" * 16, "size": 4}

        def catalogs(game, tool, output):
            self.assertEqual(game, self.game)
            output.mkdir()
            for name in CLIENT_CATALOGS:
                (output / name).write_text(
                    '{"00000000006E.075":{"IsHero":true,"Name":"Fixture"}}', encoding="utf-8",
                )

        def datatool(tool, game, mode, *args):
            self.assertEqual(game, self.game)
            if mode == "dump-strings":
                self.assertEqual(args[-1], "--language=enUS")
                strings = Path(args[1].removeprefix("--out="))
                strings.parent.mkdir(parents=True)
                strings.write_text("{}", encoding="utf-8")
                return
            output, asset, flag = args
            self.assertEqual((mode, flag), ("extract-stu-type", "--xml"))
            folder = Path(output) / asset
            folder.mkdir(parents=True)
            if asset == "075":
                (folder / "00000000006E.075.xml").write_text(
                    '<Asset hml:name="m_gameplayEntity" GUID="0000000003CF.003"/>', encoding="utf-8",
                )
            elif asset == "0C7":
                flags = " ".join(f'{name}="0"' for name in ASSET_FLAGS)
                (folder / "000000000001.0C7.xml").write_text(f"<Asset {flags}/>", encoding="utf-8")
            elif asset == "003":
                (folder / "0000000003CF.003.xml").write_text("<Asset/>", encoding="utf-8")

        def run(command, **options):
            if "-MapChunks" in command:
                self.assertEqual(command[-1], chunk_key)
                data = {"schema": RECORD_CATALOG_SCHEMA, "build": RECORD_CATALOG_BUILD}
                data["records"] = {chunk_key: record}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data).encode("utf-8"))
            self.assertEqual(command[-3:], ["0688", "--game", str(self.game)])
            staging = Path(options["env"]["ORPHEUS_RUNTIME_DIR"])
            self.assertTrue((staging / "data" / "collision_props_174.json").is_file())
            self.assertFalse((staging / "data" / "lobby_state_blob.key").exists())
            index = json.loads((staging / "data" / "collision_keys_174.json").read_text(encoding="utf-8"))
            self.assertEqual(index, {map_key: record})
            cache = staging / "collision"
            cache.mkdir()
            (cache / "0688.col").write_bytes(b"generated collision fixture")

        def spawners(xml, storage, props, names, *, maps):
            self.assertEqual(maps, {scene.map_guid})
            self.assertEqual(props, self.data / "collision_props_174.json")
            self.assertTrue(names.is_file())
            self.assertTrue((xml / "003").is_dir())
            return {"maps": {map_key: {"spawners": [{}]}}, "heroes": {}}

        def queues(xml, capture):
            self.assertEqual(capture, self.data / "retail_templates.json")
            self.assertTrue((xml / "07C" / "strings_enUS.json").is_file())
            self.assertTrue((xml / "0C0").is_dir())
            return {"cards": {"fixture": {}}, "skipped": {}, "variants": {}}

        with (
            patch("tools.prepare_server_runtime.prepare_catalogs", side_effect=catalogs),
            patch("tools.prepare_client_gameplay.run_data_tool", side_effect=datatool),
            patch("tools.prepare_client_gameplay.Storage"),
            patch("tools.prepare_client_gameplay.extract_ai_spawners.collect", side_effect=spawners),
            patch("tools.prepare_client_gameplay.extract_game_queues.collect", side_effect=queues),
            patch("tools.prepare_map_data.shutil.which", return_value="pwsh"),
            patch("tools.prepare_server_runtime.subprocess.run", side_effect=run),
        ):
            prepare(
                self.source, self.destination, ["00000688"],
                game=self.game, data_tool=self.tool, owlib=self.folder,
            )

        data = self.destination / "data"
        heroes = json.loads((data / "game_heroes_174.json").read_text(encoding="utf-8"))
        self.assertEqual(
            list(heroes.values()), [{"name": "Fixture", "class": "", "body": "0x04000000000003CF"}],
        )
        self.assertIn("0x1", json.loads((data / "arcade_cards_174.json").read_text(encoding="utf-8")))
        scene_data = json.loads((data / "game_maps_174.json").read_text(encoding="utf-8"))
        self.assertEqual(scene_data[map_key]["placeables"], list(scene.placeables))
        ai_data = json.loads((data / "ai_spawners_174.json").read_text(encoding="utf-8"))
        self.assertIn(map_key, ai_data["maps"])
        queue_data = json.loads((data / "game_queues_174.json").read_text(encoding="utf-8"))
        self.assertEqual(queue_data["cards"], {"fixture": {}})
        self.assertEqual((data / "extracted_maps.json").read_text(encoding="utf-8"), "prepared fixture")
        self.assertEqual((data / "game_graphs_174.json.gz").read_text(encoding="utf-8"), "prepared fixture")
        cache = self.destination / "collision" / "0688.col"
        self.assertEqual(cache.read_bytes(), b"generated collision fixture")
        self.assertFalse(any((self.data / name).exists() for name in missing))

    def test_missing_prepared_inputs_fail_before_generation(self):
        (self.data / "retail_templates.json").unlink()
        (self.data / "extracted_heroes.json").unlink()
        with (
            patch("tools.prepare_server_runtime.prepare_catalogs") as generate,
            self.assertRaisesRegex(ValueError, r"Missing runtime inputs: data/retail_templates\.json"),
        ):
            prepare(self.source, self.destination, ["0688"], game=self.game, data_tool=Path("DataTool.exe"))
        generate.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_multiple_maps_still_require_the_full_prepared_map_table(self):
        (self.data / "game_maps_174.json").unlink()
        with self.assertRaisesRegex(ValueError, r"Missing runtime inputs: data/game_maps_174\.json"):
            prepare(self.source, self.destination, ["0688", "0CD3"], game=self.game)
        self.assertFalse(self.destination.exists())

    def test_new_prop_index_rebuilds_the_base_cache_and_leaves_old_rays_out(self):
        (self.data / "collision_props_174.json").unlink()
        cache = self.source / "cache" / "collision"
        cache.mkdir(parents=True)
        (cache / "0688.col").write_bytes(b"old geometry")
        (cache / "0688.rays.col").write_bytes(b"old ray geometry")

        def props(game, tool, output):
            self.assertEqual((game, tool), (self.game, self.tool))
            output.write_text('{"generated":true}', encoding="utf-8")

        def collision(staging, game, maps, inputs):
            self.assertEqual([path.name for path in maps], ["0688.col"])
            self.assertEqual(inputs["data/collision_props_174.json"].read_text(encoding="utf-8"),
                             '{"generated":true}')
            staging.mkdir()
            (staging / "0688.col").write_bytes(b"new geometry")
            return staging

        with (
            patch("tools.prepare_server_runtime.write_practice_props", side_effect=props),
            patch("tools.prepare_server_runtime._build_collision", side_effect=collision),
        ):
            prepare(self.source, self.destination, ["0688"], game=self.game, data_tool=self.tool)
        self.assertEqual((self.destination / "collision" / "0688.col").read_bytes(), b"new geometry")
        self.assertFalse((self.destination / "collision" / "0688.rays.col").exists())
        self.assertEqual((cache / "0688.col").read_bytes(), b"old geometry")

    def test_practice_props_keep_only_non_client_blocking_root_models(self):
        entities = 0x0DD0000B00000688
        definition = 0x0400000000000100
        blocking = 0x0D00000000000200
        decoration = blocking + 1
        client_only = blocking + 2
        placement_record = {"fixture": "placement"}
        assets = {
            definition: {"fixture": "definition"},
            blocking: {"fixture": "blocking"},
            decoration: {"fixture": "decoration"},
        }
        output = self.folder / "props.json"
        with (
            patch(
                "tools.prepare_map_data._local_records", side_effect=[{entities: placement_record}, assets],
            ),
            patch("tools.prepare_map_data.Storage") as storage,
            patch("tools.prepare_map_data.props.placements", return_value=[(definition, 0)]),
            patch("tools.prepare_map_data.props.entity_model_refs",
                  return_value=([(blocking, False), (decoration, False), (client_only, True)], [])),
            patch("tools.prepare_map_data.props.model_shapes", side_effect=[[object()], []]),
        ):
            write_practice_props(self.game, self.tool, output)
        self.assertEqual(storage.return_value.read_record.call_count, 4)
        data = json.loads(output.read_text(encoding="utf-8"))
        model = f"0x{blocking:016X}"
        self.assertEqual(data["maps"], {f"0x{PRACTICE_RANGE.map_guid:016X}": placement_record})
        self.assertEqual(data["defs"], {f"0x{definition:016X}": model})
        self.assertEqual(data["models"], {model: assets[blocking]})
