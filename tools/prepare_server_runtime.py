"""Assemble a runtime from authorized inputs and optionally generate missing client data."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game.casc import CascError, game_root
from ow174.game.content import PRACTICE_RANGE
from ow174.game.props import PropError
from ow174.paths import (
    CLIENT_CATALOGS,
    EMOTE_ALIAS_DATA,
    EMOTE_BLEND_TREE_DATA,
    EMOTE_CLIP_DATA,
    EMOTE_DURATION_DATA,
    EMOTE_SCALAR_DATA,
    EMOTE_TAGGED_DATA,
    GREETING_DURATION_DATA,
    LOBBY_RUNTIME_DATA,
    MATCH_RUNTIME_DATA,
    ROOT,
    VOICE_EFFECT_DATA,
    WHEEL_POSE_DATA,
)
from tools.prepare_client_catalogs import prepare_catalogs, require_target_build
from tools.prepare_client_gameplay import XML_TYPES, export_xml, prepare_gameplay
from tools.prepare_map_data import write_collision_index, write_practice_map, write_practice_props


def prepare(
    source: Path,
    destination: Path,
    maps: list[str],
    *,
    game_graphs: Path | None = None,
    catalogs: Path | None = None,
    game: Path | None = None,
    data_tool: Path | None = None,
    owlib: Path | None = None,
    client_xml: Path | None = None,
) -> None:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_dir():
        raise ValueError("The prepared checkout does not exist")
    if destination.is_relative_to(source) or destination.exists():
        raise ValueError("Choose a new destination outside the source checkout")
    if data_tool is not None and game is None:
        raise ValueError("--data-tool requires --game")
    if owlib is not None and client_xml is None and data_tool is None:
        raise ValueError("--owlib requires --client-xml or --game with --data-tool")
    if game is not None:
        game = game_root(game.resolve())
        require_target_build(game)
        if destination.is_relative_to(game):
            raise ValueError("Choose a destination outside the game installation")

    inputs = {f"data/{name}": source / "data" / name for name in LOBBY_RUNTIME_DATA + MATCH_RUNTIME_DATA}
    if catalogs is not None:
        inputs.update({f"data/{name}": catalogs / name for name in CLIENT_CATALOGS})
    for name in (EMOTE_ALIAS_DATA, EMOTE_BLEND_TREE_DATA, EMOTE_CLIP_DATA, EMOTE_DURATION_DATA,
                 EMOTE_SCALAR_DATA, EMOTE_TAGGED_DATA, GREETING_DURATION_DATA, VOICE_EFFECT_DATA):
        metadata = source / "data" / name
        if metadata.is_file():
            inputs[f"data/{name}"] = metadata
    if f"data/{EMOTE_CLIP_DATA}" in inputs:
        for group, extension in (("models", "00C"), ("clips", "006")):
            folder = source / "data" / WHEEL_POSE_DATA / group
            for asset in folder.glob(f"*.{extension}"):
                if asset.is_file() and re.fullmatch(rf"[0-9A-F]{{12}}[.]{extension}", asset.name):
                    inputs[f"data/{WHEEL_POSE_DATA}/{group}/{asset.name}"] = asset
    if game_graphs is not None:
        inputs["data/game_graphs_174.json.gz"] = game_graphs
    inputs["bnet/descriptors.pb"] = source / "ow174" / "bnet" / "descriptors.pb"
    cache = source / "cache" / "collision"
    if maps:
        if any(not re.fullmatch("[0-9a-fA-F]{4,8}", value) for value in maps):
            raise ValueError("Map indexes must contain four to eight hexadecimal digits")
        caches = [cache / f"{index:04X}.col" for index in dict.fromkeys(int(value, 16) for value in maps)]
    else:
        caches = sorted(path for path in cache.glob("*.col") if re.fullmatch("[0-9a-fA-F]{4,8}", path.stem))
    selected_maps = {0x0800000000000000 | int(path.stem, 16) for path in caches}
    practice_only = bool(maps) and selected_maps == {PRACTICE_RANGE.map_guid}
    prop_data = "data/collision_props_174.json"
    generate_props = (
        practice_only and game is not None and data_tool is not None and not inputs[prop_data].is_file()
    )
    inputs.update({f"collision/{path.name}": path for path in caches})
    for path in caches:
        rays = path.with_suffix(".rays.col")
        if not generate_props and rays.is_file():
            inputs[f"collision/{rays.name}"] = rays
    missing_catalogs = {
        name for name in CLIENT_CATALOGS if data_tool is not None and catalogs is None
        and not inputs[f"data/{name}"].is_file()
    }
    missing_gameplay = {
        name for name in XML_TYPES if (owlib is not None or client_xml is not None)
        and (name != "game_graphs_174.json.gz" or (owlib is not None and game_graphs is None))
        and (name != "ai_spawners_174.json" or game is not None)
        and not inputs[f"data/{name}"].is_file()
    }
    missing_caches = [path for path in caches if game is not None and (generate_props or not path.is_file())]
    generated = {f"data/{name}" for name in missing_catalogs | missing_gameplay}
    generated.update(f"collision/{path.name}" for path in missing_caches)
    map_data = "data/game_maps_174.json"
    index_data = "data/collision_keys_174.json"
    if practice_only and not inputs[map_data].is_file():
        generated.add(map_data)
    if generate_props:
        generated.add(prop_data)
    if game is not None and data_tool is not None and selected_maps and not inputs[index_data].is_file():
        generated.add(index_data)
    missing = [name for name, path in inputs.items() if name not in generated and not path.is_file()]
    if missing:
        raise ValueError("Missing runtime inputs: " + ", ".join(missing))
    if not caches:
        raise ValueError("No prepared collision caches were found. Select --map 0688 for Practice Range")

    with tempfile.TemporaryDirectory(prefix="orpheus-client-data-") as folder:
        staging = Path(folder)
        if map_data in generated:
            inputs[map_data] = staging / Path(map_data).name
            write_practice_map(inputs[map_data])
        if index_data in generated:
            assert game is not None and data_tool is not None
            inputs[index_data] = staging / Path(index_data).name
            write_collision_index(game, data_tool, selected_maps, inputs[index_data])
        if generate_props:
            assert game is not None and data_tool is not None
            inputs[prop_data] = staging / Path(prop_data).name
            write_practice_props(game, data_tool, inputs[prop_data])
        if missing_catalogs:
            assert game is not None and data_tool is not None
            local_catalogs = staging / "catalogs"
            prepare_catalogs(game, data_tool, local_catalogs)
            inputs.update({f"data/{name}": local_catalogs / name for name in missing_catalogs})
        if missing_gameplay:
            if client_xml is None:
                assert game is not None and data_tool is not None
                client_xml = staging / "xml"
                export_xml(game, data_tool, client_xml, missing_gameplay)
            data_paths = {
                Path(name).name: path for name, path in inputs.items() if name.startswith("data/")
            }
            gameplay = prepare_gameplay(
                client_xml, owlib, staging / "gameplay", data_paths, missing_gameplay,
                game=game, maps=selected_maps,
            )
            inputs.update({f"data/{name}": path for name, path in gameplay.items()})
        if missing_caches:
            assert game is not None
            local_cache = _build_collision(staging / "map-runtime", game, missing_caches, inputs)
            inputs.update({f"collision/{path.name}": local_cache / path.name for path in missing_caches})
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".orpheus-setup-", dir=destination.parent) as output_dir:
            output = Path(output_dir) / "runtime"
            _write_runtime(output, inputs)
            output.rename(destination)


def _build_collision(
    staging: Path, game: Path, maps: list[Path], inputs: dict[str, Path],
) -> Path:
    (staging / "data").mkdir(parents=True)
    for name in ("collision_keys_174.json", "collision_props_174.json", "game_maps_174.json"):
        relative = f"data/{name}"
        shutil.copyfile(inputs[relative], staging / relative)
    subprocess.run(
        [
            sys.executable, "-B", str(ROOT / "tools" / "build_collision.py"),
            *(path.stem for path in maps), "--game", str(game),
        ],
        env={**os.environ, "ORPHEUS_RUNTIME_DIR": str(staging)},
        check=True,
        timeout=600,
    )
    return staging / "collision"


def _write_runtime(destination: Path, inputs: dict[str, Path]) -> None:
    destination.mkdir()
    manifest = {}
    for name, path in sorted(inputs.items()):
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
        with output.open("rb") as handle:
            manifest[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    (destination / "manifest.json").write_text(
        json.dumps({"schema": "orpheus-runtime-copy-v1", "sha256": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="prepared local research checkout")
    parser.add_argument("--destination", type=Path, required=True, help="new directory outside the checkout")
    parser.add_argument("--map", dest="maps", action="append", default=[], help="map index such as 0688")
    parser.add_argument("--game-graphs", type=Path, help="local graph bundle for the new pack")
    parser.add_argument("--catalogs", type=Path, help="local directory from prepare_client_catalogs.py")
    parser.add_argument("--game", type=Path, help="104319 install for missing client data and map caches")
    parser.add_argument("--data-tool", type=Path, help="pinned DataTool.exe for local game exports")
    parser.add_argument("--owlib", type=Path, help="pinned OWLib checkout to enable gameplay XML generation")
    parser.add_argument("--client-xml", type=Path, help="use existing local 104319 DataTool XML")
    args = parser.parse_args()
    try:
        prepare(
            args.source, args.destination, args.maps, game_graphs=args.game_graphs, catalogs=args.catalogs,
            game=args.game, data_tool=args.data_tool, owlib=args.owlib, client_xml=args.client_xml,
        )
    except (CascError, OSError, PropError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(f"Prepared runtime data at {args.destination.resolve()}")


if __name__ == "__main__":
    main()
