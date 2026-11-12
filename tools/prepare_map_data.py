"""Prepare map data from the selected local game storage and the server's built-in Practice Range."""

import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from ow174.game import props
from ow174.game.casc import RecordCatalog, Storage
from ow174.game.collision import chunk_guid
from ow174.game.content import PRACTICE_RANGE
from ow174.paths import ROOT


def write_practice_map(output: Path) -> None:
    scene = PRACTICE_RANGE
    data = {
        f"0x{scene.map_guid:016X}": {
            "name": scene.name,
            "modes": [f"0x{scene.mode_guid:016X}"],
            "placeables": scene.placeables,
            "spawns": [asdict(spawn) for spawn in scene.spawns],
        },
    }
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_collision_index(game: Path, data_tool: Path, maps: set[int], output: Path) -> None:
    chunks = {map_guid: chunk_guid(map_guid) for map_guid in sorted(maps)}
    records = _local_records(
        game, data_tool, output.with_name("collision-records.json"),
        "-MapChunks", _guid_list(chunks.values()), map_chunks=True,
    )
    if records.keys() != set(chunks.values()):
        raise ValueError("The local collision index does not cover the selected map chunks")
    data = {f"0x{map_guid:016X}": records[guid] for map_guid, guid in chunks.items()}
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_practice_props(game: Path, data_tool: Path, output: Path) -> None:
    scene = PRACTICE_RANGE
    entities = 0x0DD0000B00000000 | (scene.map_guid & 0xFFFFFFFF)
    records = _local_records(
        game, data_tool, output.with_name("practice-placement-record.json"),
        "-MapChunks", _guid_list([entities]), map_chunks=True,
    )
    if records.keys() != {entities}:
        raise ValueError("The local export is missing the Practice Range entity chunk")
    storage = Storage(game)
    placement = records[entities]
    placed = props.placements(storage.read_record(placement))
    if not placed or any(identifier for _, identifier, *_ in placed):
        raise ValueError("Expected the Practice Range's unfiltered client placements")
    definitions = sorted({definition for definition, *_ in placed if definition >> 48 == 0x0400})
    if not definitions:
        raise ValueError("The local Practice Range contains no prop definitions")

    definitions_to_models = {}
    models = {}
    blocking = {}
    for start in range(0, len(definitions), 128):
        batch = definitions[start : start + 128]
        assets = _local_records(
            game, data_tool, output.with_name("practice-prop-records.json"),
            "-AssetGuidList", _guid_list(batch), "-IncludeRootModels",
        )
        for definition in batch:
            if definition not in assets:
                raise ValueError(f"Missing prop definition {definition:#018x} in local export")
            refs, _ = props.entity_model_refs(storage.read_record(assets[definition]))
            roots = sorted({model for model, client_only in refs if model and not client_only})
            movement_models = []
            for model in roots:
                if model not in assets:
                    raise ValueError(f"Missing root model {model:#018x} in local export")
                if model not in blocking:
                    blocking[model] = bool(props.model_shapes(storage.read_record(assets[model])))
                if blocking[model]:
                    movement_models.append(model)
            if len(movement_models) > 1:
                raise ValueError(f"Prop definition {definition:#018x} has several blocking root models")
            if movement_models:
                model = movement_models[0]
                model_key = f"0x{model:016X}"
                definitions_to_models[f"0x{definition:016X}"] = model_key
                models[model_key] = assets[model]
    if not models:
        raise ValueError("The local Practice Range export produced no movement collision props")
    data = {
        "_source": "Local build 104319 Practice Range movement collision props",
        "maps": {f"0x{scene.map_guid:016X}": placement},
        "defs": definitions_to_models,
        "models": models,
        "modes": {f"0x{scene.mode_guid:016X}": []},
    }
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _guid_list(guids) -> str:
    return ",".join(f"0x{guid:016X}" for guid in guids)


def _local_records(
    game: Path, data_tool: Path, catalog_path: Path, *arguments: str, map_chunks: bool = False,
) -> dict[int, dict]:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        raise ValueError("Building the local collision index requires PowerShell 7 (pwsh)")
    if not data_tool.is_file():
        raise ValueError("Select the built DataTool.exe from the pinned OWLib checkout")
    response = subprocess.run(
        [
            pwsh, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "tools" / "export_casc_records.ps1"),
            "-GameRoot", str(game), "-DataToolDir", str(data_tool.resolve().parent),
            *arguments,
        ],
        check=True,
        timeout=300,
        stdout=subprocess.PIPE,
    )
    catalog_path.write_bytes(response.stdout)
    return RecordCatalog.load(catalog_path, collision_chunks=map_chunks).records
