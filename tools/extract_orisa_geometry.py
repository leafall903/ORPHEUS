"""Read the checked barrier model from CASC; write only the requested external geometry file."""

import argparse
import hashlib
import json
from pathlib import Path

from ow174.game import props
from ow174.game.casc import RecordCatalog, Storage

MODEL = 0x0D00000000004062
MODELS = {"barrier": MODEL, "supercharger": 0x0D000000000042A2}
SCHEMA = "orpheus-orisa-geometry-v1"


def extract(game_root, records, model=MODEL):
    catalog = RecordCatalog.load(records)
    raw = Storage(game_root).read_record(catalog.records[model])
    shapes = props.model_shapes(raw, category=0xFFFF, static_only=False)
    triangles = [item for shape in shapes for item in props.shape_triangles(shape)]
    if not triangles:
        raise ValueError("Orisa deployable model has no collision geometry")
    return {
        "schema": SCHEMA, "build": "1.74.0.0.104319", "model": f"0x{model:016X}",
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "catalog_sha256": catalog.sha256,
        "triangles": [list(a) + list(b) + list(c) for a, b, c, _mask in triangles],
        "masks": [mask for _a, _b, _c, mask in triangles],
        "native_runtime_filter_policy_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", required=True)
    parser.add_argument("--records", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kind", choices=MODELS, default="barrier")
    args = parser.parse_args()
    result = extract(args.game_root, args.records, MODELS[args.kind])
    output = Path(args.output).resolve()
    game = Path(args.game_root).resolve()
    if output == game or game in output.parents:
        parser.error("output must be outside the game install")
    output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(result['triangles'])} triangles -> {output}")


if __name__ == "__main__":
    main()
