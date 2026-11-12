"""Generate the four ORPHEUS catalogs that come directly from a local 104319 game install."""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from ow174.game.casc import CascError, game_root
from ow174.paths import CLIENT_CATALOGS, ROOT

TARGET_BUILD = "1.74.0.0.104319"
MODES = ("list-unlocks", "list-general-unlocks", "list-heroes", "list-maps")


def require_target_build(root: Path) -> None:
    with (root / ".build.info").open(encoding="utf-8-sig", newline="") as file:
        builds = {
            row.get("Version!STRING:0")
            for row in csv.DictReader(file, delimiter="|")
            if row.get("Active!DEC:1") == "1" and row.get("Product!STRING:0", "pro") == "pro"
        }
    if builds != {TARGET_BUILD}:
        found = ", ".join(sorted(build for build in builds if build)) or "no active retail version"
        raise ValueError(f"Expected Overwatch {TARGET_BUILD} in .build.info, found {found}")


def run_data_tool(data_tool: Path, root: Path, *args: str) -> None:
    data_tool = data_tool.resolve()
    if not data_tool.is_file():
        raise ValueError("Select the built DataTool.exe from the pinned OWLib checkout")
    subprocess.run(
        [
            str(data_tool), str(root), *args, "--args-reset", "--skip-keys", "--online=false",
            "--cache=false", "--cache-data=false", "--allow-manifest-fallback=false",
        ],
        check=True,
        timeout=300,
    )


def prepare_catalogs(game: Path, data_tool: Path, destination: Path) -> None:
    root = game_root(game.resolve())
    require_target_build(root)
    destination = destination.resolve()
    if destination.exists() or destination.is_relative_to(ROOT) or destination.is_relative_to(root):
        raise ValueError("Choose a new destination outside the ORPHEUS and game directories")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        staging = Path(folder) / "catalogs"
        staging.mkdir()
        for name, mode in zip(CLIENT_CATALOGS, MODES, strict=True):
            output = staging / name
            run_data_tool(data_tool, root, mode, "--json", f"--out={output}")
            data = json.loads(output.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not data:
                raise ValueError(f"DataTool produced an empty or invalid catalog for {name}")
        staging.rename(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True, help="game folder or Overwatch.exe")
    parser.add_argument("--data-tool", type=Path, required=True, help="DataTool.exe built from pinned OWLib")
    parser.add_argument("--destination", type=Path, required=True, help="new local catalog directory")
    args = parser.parse_args()
    try:
        prepare_catalogs(args.game, args.data_tool, args.destination)
    except (CascError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(f"Generated four local catalogs in {args.destination.resolve()}")


if __name__ == "__main__":
    main()
