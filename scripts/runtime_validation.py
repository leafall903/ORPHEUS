"""Verify a prepared local 104319 runtime directory before it is shared."""

import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import zlib

from runtime_projectiles import persistent_entity_issue


GRAPH = "data/game_graphs_174.json.gz"
PRACTICE_MODE = "0x0230000000000018"


def stock_model_issue(bodies):
    """Generation requires both kinds of reference data on every stock skin."""
    if len(bodies) != 32:
        return f"Expected 32 stock 104319 hero bodies, found {len(bodies)}"
    missing = []
    for key, body in bodies.items():
        rows = body.get("model_hardpoints") if isinstance(body, dict) else None
        if not isinstance(rows, list) or not any(
            isinstance(row, dict) and row.get("skin") == "0x0000000000000000"
            and row.get("reference_positions") and row.get("reference_bones")
            for row in rows
        ):
            missing.append(key)
    if missing:
        return "Missing stock model attachment positions or bone transforms for " + ", ".join(sorted(missing))
    return None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"repeated manifest key: {key}")
        result[key] = value
    return result


def _pack_file(root, name):
    # Runtime manifests use forward slashes on every host, including Windows.
    relative = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        not name or relative.as_posix() != name or windows.anchor or "\\" in name
        or any(
            part == ".." or part.endswith((".", " ")) or re.search(r'[:*?"<>|\x00-\x1f]', part)
            or PureWindowsPath(part).is_reserved()
            for part in relative.parts
        )
    ):
        raise ValueError(f"invalid manifest path: {name!r}")
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"manifest path does not identify a file inside the runtime: {name!r}")
    return resolved


def _verify_manifest(root):
    manifest_path = _pack_file(root, "manifest.json")
    with manifest_path.open(encoding="utf-8") as source:
        manifest = json.load(source, object_pairs_hook=_unique)
    if not isinstance(manifest, dict) or manifest.get("schema") != "orpheus-runtime-copy-v1":
        raise ValueError("expected an orpheus-runtime-copy-v1 manifest")
    entries = manifest.get("sha256")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("expected a nonempty manifest SHA-256 inventory")
    seen = set()
    for name, expected in entries.items():
        file = _pack_file(root, name)
        if file in seen or file == manifest_path:
            raise ValueError(f"duplicate or self-referencing manifest file: {name!r}")
        seen.add(file)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(f"invalid SHA-256 for {name}")
        with file.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected.lower():
            raise ValueError(f"SHA-256 mismatch for {name}")
    return entries


def verify_manifest(path: Path) -> dict[str, str]:
    """Return verified file names and declared digests for a runtime or supplement."""
    try:
        return _verify_manifest(Path(path).resolve(strict=True))
    except (OSError, RuntimeError) as error:
        raise ValueError(f"runtime manifest validation failed: {error}") from error


def _check_payload_inventory(root, entries):
    # The packer dedicates these directories to inputs read by the server.
    # Walk nested mode data as well as optional files that are absent from the preflight list.
    directories = [root / name for name in ("data", "bnet", "collision") if (root / name).is_dir()]
    visited = set()
    while directories:
        folder = directories.pop()
        actual = folder.resolve(strict=True)
        if not actual.is_relative_to(root) or actual in visited:
            raise ValueError(f"payload directory escapes or aliases the runtime: {folder.relative_to(root)}")
        visited.add(actual)
        for item in folder.iterdir():
            if item.is_dir():
                directories.append(item)
            elif (name := item.relative_to(root).as_posix()) not in entries:
                raise ValueError(f"unlisted runtime payload: {name}")


def _resource_id(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(rf"0x{prefix}[0-9a-fA-F]{{12}}", value, re.I):
        return None
    return int(value[6:], 16) or None


def _check_graphs(bundle):
    if not isinstance(bundle, dict) or bundle.get("version") != 1:
        raise ValueError("expected a version 1 StateScript graph bundle")
    rows = bundle.get("graphs")
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing prepared graphs")
    indices = {row["index"] for row in rows if isinstance(row, dict) and type(row.get("index")) is int}
    scripts = bundle.get("mode_scripts")
    mode = scripts.get(PRACTICE_MODE) if isinstance(scripts, dict) else None
    if not isinstance(mode, dict) or not isinstance(mode.get("teams"), dict):
        raise ValueError("missing Practice Range mode_scripts")
    refs = [mode.get("graph")]
    if mode.get("shared_graph") is not None:
        refs.append(mode["shared_graph"])
    for team in ("0", "1"):
        bindings = mode["teams"].get(team)
        if not isinstance(bindings, dict):
            raise ValueError(f"missing Practice Range mode_scripts for team {team}")
        refs.extend((bindings.get("body"), bindings.get("controller")))
    if any(_resource_id(ref, "0580") not in indices for ref in refs):
        raise ValueError("Practice Range mode_scripts refer to a missing graph")
    variables = bundle.get("mode_variables")
    if not isinstance(variables, dict) or not isinstance(variables.get(PRACTICE_MODE), dict) or not variables[PRACTICE_MODE]:
        raise ValueError("missing Practice Range mode_variables")
    rulesets = bundle.get("mode_rulesets")
    if not isinstance(rulesets, list) or not any(
        isinstance(row, dict) and row.get("mode") == PRACTICE_MODE
        and isinstance(row.get("rules"), list) and row["rules"]
        for row in rulesets
    ):
        raise ValueError("missing Practice Range mode_rulesets")
    bodies = bundle.get("bodies")
    if not isinstance(bodies, dict) or len(bodies) != 32:
        raise ValueError("expected 32 prepared hero bodies for build 104319")
    ids = set()
    for body, record in bodies.items():
        body_id = _resource_id(body, "0400")
        if body_id is None or body_id in ids:
            raise ValueError(f"invalid or duplicate hero body: {body}")
        ids.add(body_id)
        tags = record.get("target_tags") if isinstance(record, dict) else None
        if not isinstance(tags, list) or not tags or any(_resource_id(tag, "0D60") is None for tag in tags):
            raise ValueError(f"missing or invalid target_tags for hero body {body}")
    missing = bundle.get("missing", [])
    if not isinstance(missing, list) or any(type(index) is not int or index < 0 for index in missing):
        raise ValueError("invalid graph bundle missing-reference list")
    if missing:
        print("Referenced graphs absent from this bundle: " + ", ".join(f"{index:04X}" for index in missing))


def validate_pack(path: Path, *, require_gameplay_data: bool = False) -> None:
    """Verify the runtime and report stock model coverage for gameplay."""
    try:
        root = Path(path).resolve(strict=True)
        entries = verify_manifest(root)
        if GRAPH not in entries:
            raise ValueError(f"manifest SHA-256 inventory must include {GRAPH}")
        _check_payload_inventory(root, entries)
        with gzip.open(_pack_file(root, GRAPH), "rt", encoding="utf-8") as source:
            graph = json.load(source)
        _check_graphs(graph)
        issues = []
        if model_issue := stock_model_issue(graph["bodies"]):
            issues.append(model_issue)
        else:
            print("Stock model attachments and bone transforms: all 32 default hero models present.", flush=True)
        if projectile_issue := persistent_entity_issue(graph):
            issues.append(projectile_issue)
    except (OSError, EOFError, RuntimeError, zlib.error) as error:
        raise ValueError(f"runtime validation failed: {error}") from error

    if issues:
        message = ("\n".join(issues) + "\nRebuild from the installation with the old pack as input:\n"
                   '  python scripts/runtime.py build "<OW1 folder>" --inputs "<old pack>" --output "<new directory>"')
        if require_gameplay_data:
            raise ValueError(message)
        print(message, flush=True)
