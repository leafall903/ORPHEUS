"""Client-derived stages for scripts/runtime.py. Source builds and data caches are private."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build_runtime_state_metadata import import_model_hardpoints, import_model_skeletons, write_bundle
from runtime_tools import extraction_tools
from runtime_status import STATUS_SCRIPTS, status_scripts
from runtime_validation import stock_model_issue
from runtime_voice import VOICE_CATALOGS, user_locale, voice_locales


def variant(files):
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(file.name.encode() + b"\0")
        with file.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()[:16]


def checkpoint(dest, produce):
    """Publish each completed export once; a failed process leaves the last good stage intact."""
    if dest.is_dir():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating {dest.name} ...", flush=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".extract-") as tmp:
        output = Path(tmp) / "result"
        produce(output)
        try:
            output.rename(dest)
        except OSError:
            if not dest.is_dir():
                raise
    return dest


def sdk_tool(project, scratch, dependencies):
    workspace = scratch / project.stem
    binary = workspace / "release" / (project.stem + ".dll")
    if not binary.exists():
        workspace.mkdir()
        for file in (project, *project.parent.glob("*.cs")):
            shutil.copyfile(file, workspace / file.name)
        subprocess.run(["dotnet", "build", str(workspace / project.name), "-c", "Release",
                        f"-p:DataToolDir={dependencies}", "--output", str(binary.parent)], check=True)
    return ["dotnet", str(binary), str(dependencies)]


def import_stock_models(graph, directory):
    points = import_model_hardpoints(graph, directory / "stock-model-anchors.json")
    bones = import_model_skeletons(graph, directory / "stock-skeletons.json")
    if issue := stock_model_issue(graph["bodies"]):
        raise ValueError(issue)
    return points, bones


def generate(game, cache, raw, view, scratch, mode, tags, anchors, need_rules, projectiles):
    from runtime import GRAPH, ROOT, VERSION, link_input
    from runtime_validation import PRACTICE_COLLISION

    reference, owlib, tool = extraction_tools(cache / "tools")
    sys.path[:0] = [str(reference), str(reference / "src")]
    from ow174.game.content import PRACTICE_RANGE
    from ow174.paths import CLIENT_CATALOGS
    from tools import extract_game_graphs, prepare_client_gameplay as gameplay, prepare_map_data as maps
    from tools.prepare_client_catalogs import prepare_catalogs, run_data_tool

    selected = {PRACTICE_RANGE.map_guid}
    data = view / "data"
    core = set(gameplay.XML_TYPES) - {GRAPH, "game_queues_174.json"}
    missing_core = {name for name in core if not (data / name).is_file()}
    need_queues = not (data / "game_queues_174.json").is_file() and (data / "retail_templates.json").is_file()

    def use(file):
        dest = data / file.name
        if not dest.is_file():
            link_input(file, dest)

    if not all((data / name).is_file() for name in CLIENT_CATALOGS):
        catalogs = checkpoint(raw / "catalogs", lambda out: prepare_catalogs(game, tool, out))
        for name in CLIENT_CATALOGS:
            use(catalogs / name)

    if any(not (data / name).is_file() for name in VOICE_CATALOGS):
        text, speech = voice_locales(game, VERSION, user_locale())
        project = ROOT / "scripts/tools/voice_catalog/VoiceCatalog.csproj"
        def export_voice(out):
            helper = sdk_tool(project, scratch, tool.parent)
            subprocess.run([*helper, str(game), text, speech, str(out)], check=True)
        schema = variant([project, *project.parent.glob("*.cs")])
        voice = checkpoint(raw / f"voice-{text}-{speech}-{schema}", export_voice)
        for name in VOICE_CATALOGS:
            use(voice / name)

    def geometry(out):
        out.mkdir()
        maps.write_practice_map(out / "game_maps_174.json")
        maps.write_collision_index(game, tool, selected, out / "collision_keys_174.json")
        maps.write_practice_props(game, tool, out / "collision_props_174.json")

    geo_names = ("game_maps_174.json", "collision_keys_174.json", "collision_props_174.json")
    if any(not (data / name).is_file() for name in geo_names):
        geo = checkpoint(raw / "practice-map", geometry)
        for name in geo_names:
            use(geo / name)
    caches = [view / name for name in PRACTICE_COLLISION]
    if any(not file.is_file() for file in caches):
        inputs = {f"data/{name}": data / name for name in geo_names}
        def collision(out):
            workspace = out.parent / "map-runtime"
            for name, file in inputs.items():
                link_input(file, workspace / name)
            env = {**os.environ, "PYTHONPATH": str(reference / "src"),
                   "ORPHEUS_RUNTIME_DIR": str(workspace)}
            command = [sys.executable, "-B", str(reference / "tools/build_collision.py"),
                       "0688", "--game", str(game)]
            for flags in ([], ["--ray-geometry"]):
                subprocess.run(command + flags, env=env, check=True)
            (workspace / "collision").rename(out)
        local = checkpoint(raw / f"collision-v2-{variant(inputs.values())}", collision)
        for file in caches:
            if not file.is_file():
                link_input(local / file.name, file)

    # Keep a separate atomic cache for each STU family. The reference reducer expects
    # one directory per asset type, so the published directories share a single parent.
    xml = raw / "xml-guid-v1"
    families = {family for name in missing_core for family in gameplay.XML_TYPES[name]}
    if not mode:
        families.update((*gameplay.XML_TYPES[GRAPH], "0C5"))
    if not tags or not projectiles:
        families.add("003")
    if not projectiles:
        families.add("01B")
    if need_rules:
        families.update(("075", "0C5", "0C6"))
    if not (data / STATUS_SCRIPTS).is_file():
        families.add("053")
    if need_queues:
        families.update(gameplay.XML_TYPES["game_queues_174.json"])

    def export_xml(out, kind):
        helper = sdk_tool(ROOT / "scripts/tools/stu_xml/StuXml.csproj", scratch, tool.parent)
        subprocess.run([*helper, str(game), kind, str(out)], check=True)
        if not any(out.glob("*.xml")):
            raise ValueError(f"DataTool returned no {kind} XML records")

    for family in sorted(families):
        checkpoint(xml / family, lambda out, kind=family: export_xml(out, kind))

    if "053" in families and not (data / STATUS_SCRIPTS).is_file():
        output = scratch / STATUS_SCRIPTS
        output.write_text(json.dumps(status_scripts(xml / "053"), allow_nan=False), encoding="utf-8")
        use(output)

    def strings(out):
        out.mkdir()
        run_data_tool(tool, game, "dump-strings", "--json", f"--out={out / 'strings_enUS.json'}", "--language=enUS")
        if not (out / "strings_enUS.json").is_file():
            raise ValueError("DataTool did not export the requested English string catalog")

    if need_queues:
        checkpoint(xml / "07C", strings)
    names = missing_core | ({"game_queues_174.json"} if need_queues else set())
    if names:
        paths = {p.name: p for p in data.iterdir()}
        for name in (*core, "game_queues_174.json"):
            paths.setdefault(name, data / name)
        dependency_names = {"collision_props_174.json", "extracted_heroes.json",
                            "ai_spawners_174.json", "game_heroes_174.json"}
        if need_queues:
            dependency_names.add("retail_templates.json")
        inputs = [data / name for name in dependency_names - names if (data / name).is_file()]
        stage = hashlib.sha256(",".join(sorted(names)).encode()).hexdigest()[:8]
        output = checkpoint(raw / f"tables-{stage}-{variant(inputs)}", lambda out:
                            gameplay.prepare_gameplay(xml, owlib, out, paths, names, game=game, maps=selected))
        for name in names:
            use(output / name)

    args = []
    if need_rules:
        from runtime_rulesets import collect_rulesets
        profiles = collect_rulesets(xml, data / "game_heroes_174.json", [f"0x{PRACTICE_RANGE.mode_guid:016X}"])
        (scratch / "mode-rulesets.json").write_text(json.dumps(profiles, allow_nan=False), encoding="utf-8")
    if not tags:
        args.extend(["--target-tags-xml", xml / "003"])
    stored = data / GRAPH
    if not mode:
        if stored.exists():
            raise ValueError(f"The supplied {GRAPH} lacks mode data. Use an inputs directory without this partial graph to regenerate it.")
        def export_graph(out):
            out.mkdir()
            graphs = extract_game_graphs.collect(
                xml, extract_game_graphs.EnumTables(owlib / "TankLib/STU/Types"),
                mode_xml=xml / "0C5", hero_table=data / "game_heroes_174.json",
                ai_spawners=data / "ai_spawners_174.json", selected_maps=selected)
            unavailable = xml / "01B/unreadable.txt"
            if unavailable.is_file():
                names = unavailable.read_text(encoding="utf-8-sig").splitlines()
                failed = {int(name.split(".")[0], 16) for name in names if name.strip()}
                required = sorted(failed.intersection(graphs["missing"]))
                if required:
                    raise ValueError("DataTool could not decode referenced graphs: " +
                                     ", ".join(f"{index:012X}.01B" for index in required))
            extract_game_graphs.write_bundle(out / GRAPH, graphs)
        sources = [data / "game_heroes_174.json", data / "ai_spawners_174.json"]
        exported = checkpoint(raw / f"graphs-guid-v1-{variant(sources)}", export_graph)
        use(exported / GRAPH)
    if not anchors or not projectiles:
        with gzip.open(stored, "rt", encoding="utf-8") as stream:
            graph = json.load(stream)
    if not projectiles:
        from runtime_projectiles import import_persistent_entities
        enums = extract_game_graphs.EnumTables(owlib / "TankLib/STU/Types")
        added = import_persistent_entities(graph, xml / "003", enums, extract_game_graphs)
        if mode and added:
            # Existing prepared graphs may include local state-group observations.
            # Supply XML only for graphs appended by this build.
            groups = scratch / "projectile-state-groups"
            groups.mkdir()
            for index in sorted(added):
                name = f"{index:012X}.01B.xml"
                link_input(xml / "01B" / name, groups / name)
            args.extend(["--state-groups-xml", groups])
        print(f"Imported {len(graph['persistent_projectiles'])} persistent projectile definitions "
              f"and {len(added)} additional StateScript graphs.", flush=True)
    if not mode:
        args.extend(["--state-groups-xml", xml / "01B"])
    if not anchors:
        hero_table = data / "game_heroes_174.json"
        def export_models(out):
            helper = sdk_tool(ROOT / "scripts/tools/model_anchors/ModelAnchors.csproj", scratch, tool.parent)
            subprocess.run([*helper, str(game), str(hero_table), str(out)], check=True)
        models = checkpoint(raw / f"models-{variant([hero_table])}", export_models)
        count, bones = import_stock_models(graph, models)
        print(f"Imported {count} attachment positions and {bones} reference bone transforms.", flush=True)
    if not anchors or not projectiles:
        output = scratch / GRAPH
        write_bundle(output, graph)
        args.extend(["--game-graphs", output])
    return args
