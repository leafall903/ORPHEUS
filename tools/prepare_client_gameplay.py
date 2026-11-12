"""Reduce local DataTool XML to gameplay data for the runtime packer."""

import json
from pathlib import Path

from ow174.game.casc import Storage
from ow174.game.content import PRACTICE_RANGE
from tools import (
    extract_ai_spawners,
    extract_arcade_cards,
    extract_game_graphs,
    extract_game_heroes,
    extract_game_movers,
    extract_game_queues,
    extract_game_stats,
)
from tools.prepare_client_catalogs import run_data_tool

XML_TYPES = {
    "ai_spawners_174.json": ("003", "075"),
    "arcade_cards_174.json": ("0C7",),
    "game_heroes_174.json": ("075",),
    "game_movers_174.json": ("003",),
    "game_graphs_174.json.gz": ("003", "01B"),
    "game_queues_174.json": ("039", "09F", "0C0", "0C5", "0C7", "0CC", "0D9", "0EE"),
    "game_stats_174.json": ("054", "062", "075"),
}


def export_xml(game: Path, data_tool: Path, xml: Path, names: set[str]) -> None:
    for asset in sorted({asset for name in names for asset in XML_TYPES[name]}):
        run_data_tool(data_tool, game, "extract-stu-type", str(xml), asset, "--xml")
    if "game_queues_174.json" in names:
        strings = xml / "07C" / "strings_enUS.json"
        run_data_tool(data_tool, game, "dump-strings", "--json", f"--out={strings}", "--language=enUS")


def prepare_gameplay(
    xml: Path, owlib: Path | None, destination: Path, data_paths: dict[str, Path], names: set[str],
    *, game: Path | None = None, maps: set[int] | None = None,
) -> dict[str, Path]:
    for asset in sorted({asset for name in names for asset in XML_TYPES[name]}):
        if not (xml / asset).is_dir():
            raise ValueError(f"Missing {asset} XML from DataTool")
    destination.mkdir()
    spawners = data_paths["ai_spawners_174.json"]
    if "ai_spawners_174.json" in names:
        assert game is not None
        spawners = destination / spawners.name
        table = extract_ai_spawners.collect(
            xml, Storage(game), data_paths["collision_props_174.json"],
            data_paths["extracted_heroes.json"], maps=maps,
        )
        practice = PRACTICE_RANGE.map_guid
        if maps is not None and practice in maps and f"0x{practice:016X}" not in table["maps"]:
            raise ValueError("The local map export contains no Practice Range spawners")
        _write_table(spawners, table)
    heroes = data_paths["game_heroes_174.json"]
    if "game_heroes_174.json" in names:
        heroes = destination / heroes.name
        _write_table(heroes, extract_game_heroes.collect(xml / "075", data_paths["extracted_heroes.json"]))
    if "arcade_cards_174.json" in names:
        _write_table(destination / "arcade_cards_174.json", extract_arcade_cards.collect(xml))
    if "game_queues_174.json" in names:
        queues = extract_game_queues.collect(xml, data_paths["retail_templates.json"])
        if not queues["cards"]:
            raise ValueError("DataTool XML did not produce any queue cards")
        _write_table(destination / "game_queues_174.json", queues)
    if "game_movers_174.json" in names:
        movers = extract_game_movers.collect(xml / "003", heroes, spawners)
        _write_table(destination / "game_movers_174.json", movers)
    if "game_stats_174.json" in names:
        stats = extract_game_stats.collect(xml, heroes)
        if not stats["stats"]:
            raise ValueError("DataTool XML did not produce any gameplay stats")
        _write_table(destination / "game_stats_174.json", stats)
    if "game_graphs_174.json.gz" in names:
        assert owlib is not None
        enums = extract_game_graphs.EnumTables(owlib / "TankLib" / "STU" / "Types")
        graphs = extract_game_graphs.collect(xml, enums, hero_table=heroes)
        if not graphs["graphs"] or not graphs["bodies"]:
            raise ValueError("DataTool XML did not produce a gameplay graph bundle")
        if graphs["missing"]:
            print(f"Local graph export recorded {len(graphs['missing'])} missing references")
        extract_game_graphs.write_bundle(destination / "game_graphs_174.json.gz", graphs)
    return {name: destination / name for name in names}


def _write_table(output: Path, data: dict) -> None:
    if not data:
        raise ValueError(f"DataTool XML did not produce any entries for {output.name}")
    output.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
