"""Prepare or verify an ORPHEUS 104319 runtime without launching the game."""

import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from runtime_validation import stock_model_issue, validate_pack

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.74.0.0.104319"
GRAPH = "game_graphs_174.json.gz"
PRIVATE_INPUTS = (
    "data/schemas_174.json", "data/game_schemas_174.json", "data/retail_templates.json",
    "data/statescript_codecs_174.json", "bnet/descriptors.pb",
    "data/resource_keys_174.json", "data/lobby_state_blob.key",
)
GENERATED = (
    "extracted_items.json", "extracted_general_unlocks.json", "extracted_heroes.json",
    "extracted_maps.json", "arcade_cards_174.json", "ai_spawners_174.json", "game_heroes_174.json",
    "game_maps_174.json", "game_movers_174.json", "game_stats_174.json", GRAPH,
    "collision_props_174.json", "collision_keys_174.json",
)


def installation(folder):
    folder = folder.resolve(strict=True)
    if folder.name.lower() == "_retail_":
        folder = folder.parent
    if not (folder / "data/casc/data").is_dir():
        raise ValueError(f"Expected an Overwatch installation containing data/casc/data: {folder}")
    with (folder / ".build.info").open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="|")
        builds = {r.get("Version!STRING:0") for r in rows
                  if r.get("Active!DEC:1") == "1" and r.get("Product!STRING:0", "pro") == "pro"}
    if builds != {VERSION}:
        raise ValueError(f"Expected active retail {VERSION}; found {', '.join(str(x) for x in builds) or 'none'}")
    return folder


def native(binary, *args):
    override = os.environ.get("ORPHEUS_SERVER_BIN" if binary == "orpheus_server" else "ORPHEUS_PACKER_BIN")
    command = [str(Path(override).resolve(strict=True))] if override else [
        "cargo", "run", "--locked", "--quiet", "--bin", binary, "--"]
    subprocess.run([*command, *map(str, args)], cwd=ROOT, check=True)


def check(pack, *, require_stock_models=False):
    validate_pack(pack, require_stock_models=require_stock_models)
    native("orpheus_server", "--runtime", pack.resolve(), "--check-runtime-graphs")
    print(f"Verified build {VERSION}, manifest hashes and Practice Range startup data: {pack}", flush=True)


def link_input(source, target):
    # The packer only reads this temporary view. Published files are separate copies.
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.hardlink_to(source)
    except FileExistsError:
        raise ValueError(f"Conflicting runtime input: {target.name}") from None
    except OSError:
        with source.open("rb") as original, target.open("xb") as copy:
            shutil.copyfileobj(original, copy)


def stage_inputs(source, view, verified=None):
    if not source.is_dir():
        return
    def available(file):
        if not file.is_file():
            return False
        relative = file.relative_to(source).as_posix()
        if verified is not None and relative not in verified:
            print(f"Skipping input absent from the source manifest: {relative}", flush=True)
            return False
        return True

    for pattern in ("data/*.json", "data/*.json.gz", "data/lobby_state_blob.key",
                    "data/junkenstein/*.json"):
        for file in source.glob(pattern):
            if available(file):
                link_input(file, view / file.relative_to(source))
    for collision in (source / "collision", source / "cache/collision"):
        if collision.is_dir():
            for file in collision.glob("*.col"):
                if re.fullmatch(r"[0-9a-f]{4,8}(?:\.rays)?\.col", file.name, re.I) and available(file):
                    index, suffix = file.name.split(".", 1)
                    link_input(file, view / "collision" / f"{index.upper()}.{suffix.lower()}")
            break
    for base in (source, source / "ow174", source / "src/ow174"):
        descriptor = base / "bnet/descriptors.pb"
        if available(descriptor):
            link_input(descriptor, view / "bnet/descriptors.pb")
            break


def graph_status(path):
    if not path.is_file():
        return False, False, False, False
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        graph = json.load(stream)
    if not isinstance(graph, dict) or not isinstance(graph.get("bodies"), dict):
        raise ValueError(f"Expected a graph bundle with a body table in {path}")
    bodies = graph.get("bodies", {})
    return (bool(graph.get("mode_scripts") and graph.get("mode_variables")),
            bool(bodies) and all(b.get("target_tags") for b in bodies.values()),
            stock_model_issue(bodies) is None, bool(graph.get("mode_rulesets")))


def raw_cache(cache, game):
    digest = hashlib.sha256(str(game).encode() + b"\0" + (game / ".build.info").read_bytes()).hexdigest()[:16]
    return cache / f"client-104319-v1-{digest}"


def outside(path, roots):
    for root in roots:
        if path.is_relative_to(root) or root.is_relative_to(path):
            raise ValueError(f"Choose separate private paths; {path} overlaps {root}")


def build(game, dest, source, cache):
    game = installation(game)
    dest, source, cache = (p.resolve() for p in (dest, source, cache))
    for path in (dest, source, cache):
        outside(path, (game, ROOT))
    outside(dest, (cache, source))
    outside(source, (cache,))
    if dest.exists():
        print("Checking the existing destination. To generate another pack, choose a new --output.", flush=True)
        check(dest, require_stock_models=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".orpheus-build-", dir=dest.parent) as scratch:
        staging = Path(scratch)
        view = staging / "inputs"
        (view / "data").mkdir(parents=True)
        (view / "collision").mkdir()
        verified = None
        if (source / "manifest.json").exists():
            from runtime_validation import verify_manifest
            verified = verify_manifest(source)
        stage_inputs(source, view, verified)
        data = view / "data"
        mode, tags, anchors, rules = graph_status(data / GRAPH)
        raw = raw_cache(cache, game)
        missing = [name for name in PRIVATE_INPUTS if not (view / name).is_file()]
        if missing:
            print("Local inputs still needed for a playable pack:\n  " + "\n  ".join(missing), flush=True)
        pack_args = []
        profiles = source / "mode-rulesets.json"
        supplied_rules = profiles.is_file() and (verified is None or "mode-rulesets.json" in verified)
        generate_rules = not rules and not supplied_rules
        if (generate_rules or not (mode and tags and anchors) or not all((data / n).is_file() for n in GENERATED)
                or not (view / "collision/0688.col").is_file()
                or not (data / "game_queues_174.json").is_file()):
            from runtime_install import generate
            pack_args = generate(game, cache, raw, view, staging, mode, tags, anchors, generate_rules)
        if not rules:
            if generate_rules:
                profiles = staging / "mode-rulesets.json"
            if not profiles.is_file():
                raise ValueError("Expected a generated mode-rulesets.json for Practice Range")
            pack_args.extend(["--mode-rulesets", profiles])
        if missing:
            raise ValueError(f"Client-derived data is cached at {raw}. Supply the following local inputs "
                             f"in {source} and rerun:\n  " + "\n  ".join(missing))
        pack = staging / "runtime"
        native("prepare_server_runtime", "--source", view, "--destination", pack, *pack_args)
        check(pack, require_stock_models=True)
        try:
            pack.rename(dest)
        except OSError:
            if not dest.is_dir():
                raise
            check(dest, require_stock_models=True)
            return
    print(f"Installed verified runtime: {dest}", flush=True)


def main(argv=None):
    app = (Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else
           Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))) / "ORPHEUS"
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    default_pack = app / "runtime-104319"
    create = subs.add_parser("build", help="generate from a local Overwatch installation")
    create.add_argument("game", type=Path)
    create.add_argument("--output", type=Path, default=default_pack)
    create.add_argument("--inputs", type=Path, default=Path(os.environ.get("ORPHEUS_RUNTIME_SOURCE", app / "inputs-104319")))
    create.add_argument("--cache", type=Path, default=app / "build-cache")
    verify = subs.add_parser("check", help="verify an existing runtime without starting the server")
    verify.add_argument("pack", type=Path, nargs="?", default=default_pack,
                        help="runtime directory (default: the build output directory)")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(args.pack.resolve())
        else:
            build(args.game, args.output, args.inputs, args.cache)
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError, EOFError) as exc:
        parser.exit(1, f"Runtime {args.command} failed: {exc}\n")


if __name__ == "__main__":
    main()
