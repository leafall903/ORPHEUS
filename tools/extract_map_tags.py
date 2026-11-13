#!/usr/bin/env python3
"""Write the target tags a map's placed entities carry, with their positions, from your own copy of the game.

The server needs them where a mode graph names a place by tag (Uprising's defend director sets its spawn
centre to the entity tagged 0x0D600000000007CA). The map's entity chunk is read straight from the game's
storage, as tools/build_collision.py does, so no DataTool is needed.

    py tools/extract_map_tags.py 0x6FE --output runtime-data/data/map_tags_174.json
    py tools/extract_map_tags.py 0x6FE --game <path to Overwatch.exe> --output <file>

A placeable's instance data (OWLib teMapChunk): a count at body+64 and, at body+112, (u32 slot, u32 offset
from the entry) pairs, each an STU v1 blob ("DUTS") with its type hash at +0x20.
"""

import argparse
import json
import struct
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ow174.game import collision, props
from ow174.game.casc import CascError, Storage, game_root

TARGET_TAG_INSTANCE = 0x64100592  # STUTargetTagInstanceData
TAG = 0x0D60


def map_tags(data: bytes) -> dict[int, list[tuple[float, float, float]]]:
    """Tag GUID -> positions of the placeables whose target-tag instance data names it, in chunk order."""
    count, _, position = struct.unpack_from("<3I", data, 0)
    records = []
    for _ in range(count):
        size = struct.unpack_from("<I", data, position + 20)[0]
        body = position + 24
        xyz = struct.unpack_from("<3f", data, body + 24)
        entries = struct.unpack_from("<I", data, body + 64)[0]
        blobs = [body + 112 + 8 * k + struct.unpack_from("<I", data, body + 116 + 8 * k)[0]
                 for k in range(entries)]
        records.append((xyz, blobs))
        if size == 0:
            break
        position += size
    starts = sorted({blob for _, blobs in records for blob in blobs} | {len(data)})
    end = dict(pairwise(starts))
    out: dict[int, list] = {}
    for xyz, blobs in records:
        for blob in blobs:
            if data[blob:blob + 4] != b"DUTS":
                raise ValueError(f"instance data at {blob:#x} is not STU v1")
            if struct.unpack_from("<I", data, blob + 0x20)[0] != TARGET_TAG_INSTANCE:
                continue
            # shortcut: tags are found as aligned raw 0D60 GUIDs in the blob, not by an STU v1 field walk;
            # upgrade if a blob ever holds a 0D60 GUID outside its tag list.
            for offset in range(blob, end[blob] - 7, 4):
                value = struct.unpack_from("<Q", data, offset)[0]
                if value >> 48 == TAG and not value & 0x0000FFFF00000000 and value & 0xFFFFFFFF:
                    out.setdefault(value, []).append(xyz)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("maps", nargs="+", help="map GUIDs or indexes (hex)")
    parser.add_argument("--game", help="Overwatch.exe or the game folder (default: game_path.txt)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = game_root(args.game) if args.game else collision.installed_game()
        if root is None:
            raise CascError("no game found in game_path.txt or --game")
        storage = Storage(root)
        maps = {}
        for name in args.maps:
            guid = 0x0800000000000000 | int(name, 16) & 0xFFFFFFFF
            record = props.placement_record(guid)
            if record is None:
                raise CascError(f"no entity chunk known for map {guid:#x}")
            tags = map_tags(storage.read_record(record))
            maps[f"0x{guid:016X}"] = {
                f"0x{tag:016X}": [list(p) for p in where] for tag, where in sorted(tags.items())
            }
            print(f"0x{guid:016X}: {len(tags)} tags on {sum(map(len, tags.values()))} placeables")
    except (CascError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    text = json.dumps({"format_version": 1, "maps": maps}, indent=1) + "\n"
    args.output.write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
