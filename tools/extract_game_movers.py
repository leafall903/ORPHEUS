#!/usr/bin/env python3
"""
Write data/game_movers_174.json: each hero body's character mover values (STUCharacterMoverComponent,
type 0x6EFDBD73), which the server's mover (ow174/game/mover.py) runs the hero with.
Also retain direct STUFirstPersonComponent standing/crouched heights for the
combat pose proxy. These adjacent metadata fields do not change the mover solver.

Reads DataTool's STU dumps of the bodies (type 003, `extract-stu-type <out> 003 --xml`) for the bodies in
data/game_heroes_174.json and the training bots' bodies in data/ai_spawners_174.json.

    py tools/extract_game_movers.py <XML dump folder>\003

To append explicit NPC definitions without rewriting prepared inputs, pass
--base <existing movers> --npc-catalog <pve_npcs_174.json> --output <new file>.
The explicit NPC path rejects missing mover definitions instead of substituting
the Soldier capsule. Prepared game data belongs outside the source repository.

The dump names the fields by hash. Their meaning comes from where the client's mover reads them: it keeps
the component's values at mover +0x418 + the field's offset (TankLib's STU layout), for example the jump
speed at +0x49C (jump 0x7FF789B48880), the gravity scale at +0x4A0 (0x7FF789AD03A0), the speeds at
+0x460..+0x474 (wish 0x7FF789B44340), the capsule at +0x430..+0x444 (stance 0x7FF789B4C3D0).
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from ow174.game.mover import STU_MOVER_FIELDS  # noqa: E402

DATA_DIR = ROOT / "data"
COMPONENT = re.compile(r"<STUCharacterMoverComponent dragon:id=\"\d+\" dragon:name=\"dragon:value\"([^>]*)/>")
ATTRIBUTE = re.compile(r'm_([0-9A-F]{8})="([^"]*)"')
FIRST_PERSON = re.compile(r'<STUFirstPersonComponent\b([^>]*)>')

# TankLib's defaults for fields a dump may leave out.
DEFAULTS = {
    "ground_circle_radius": 0.25,
    "ground_circle_rays": 4.0,
    "accel_air_touching": 0.5,
    "character_collision": 1.0,
}


def first_person_of(text: str) -> dict:
    """Direct FirstPerson component only; do not fabricate inherited/NPC views."""
    candidates = [dict(ATTRIBUTE.findall(match)) for match in FIRST_PERSON.findall(text)]
    candidates = [row for row in candidates if 'E29993A5' in row or '816B830F' in row]
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ValueError('ambiguous FirstPerson components')
    fields = candidates[0]
    values = {
        'eye_height': float(fields.get('E29993A5', '1.55').replace(',', '.')),
        'crouch_eye_height': float(fields.get('816B830F', '1.15').replace(',', '.')),
    }
    if not all(math.isfinite(value) and value > 0 for value in values.values()):
        raise ValueError('invalid FirstPerson height')
    return values


def mover_of(text: str) -> dict | None:
    match = COMPONENT.search(text)
    if match is None:
        return None
    values = dict(DEFAULTS)
    for field, value in ATTRIBUTE.findall(match.group(1)):
        if field in STU_MOVER_FIELDS:
            values[STU_MOVER_FIELDS[field]] = float(value.replace(",", "."))
    values["ground_circle_rays"] = int(values["ground_circle_rays"])
    values["character_collision"] = int(values["character_collision"])
    missing = [name for name in STU_MOVER_FIELDS.values() if name not in values]
    if missing:
        raise ValueError(f"missing {missing}")
    values.update(first_person_of(text))
    return values


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--base", type=Path,
                        help="Preserve a prepared mover catalog; append explicit NPCs only")
    parser.add_argument("--npc-catalog", type=Path,
                        help="Prepared pve_npcs_174.json with explicit body GUIDs")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "game_movers_174.json")
    args = parser.parse_args(argv[1:])
    folder = args.folder
    if args.base is not None:
        table = json.loads(args.base.read_text(encoding="utf-8"))
        if not isinstance(table, dict):
            raise ValueError("base mover catalog must be an object")
        heroes = []
    else:
        table = {}
        heroes = list(json.loads((DATA_DIR / "game_heroes_174.json").read_text(encoding="utf-8")).values())
        spawners = DATA_DIR / "ai_spawners_174.json"
        if spawners.is_file():
            heroes += json.loads(spawners.read_text(encoding="utf-8"))["heroes"].values()
    if args.npc_catalog is not None:
        catalog = json.loads(args.npc_catalog.read_text(encoding="utf-8"))
        if catalog.get("format_version") != 1 or not isinstance(catalog.get("definitions"), list):
            raise ValueError("NPC catalog must have explicit version-1 definitions")
        heroes += catalog["definitions"]
    for hero in heroes:
        body = int(hero["body"], 16)
        if body >> 48 != 0x0400 or not body & 0xFFFFFFFFFFFF:
            raise ValueError("mover catalog needs full nonzero entity definition GUIDs")
        dump = folder / f"{body & 0xFFFFFFFFFFFF:012X}.003.xml"
        values = mover_of(dump.read_text(encoding="utf-8")) if dump.is_file() else None
        if values is None:
            if args.npc_catalog is not None:
                raise ValueError(f"missing character mover for {hero['name']}: {dump}")
            print(f"skipped {hero['name']}: no character mover in {dump}")
            continue
        table[hero["body"]] = {"name": hero["name"], **values}
    out = args.output
    if args.base is not None and out.resolve() == args.base.resolve():
        raise ValueError("output must not overwrite the prepared base catalog")
    out.write_text(
        json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(table)} bodies -> {out}")


if __name__ == "__main__":
    main(sys.argv)
