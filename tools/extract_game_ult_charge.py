#!/usr/bin/env python3
"""Export ORPHEUS's built-in ultimate charge estimates as an editable local override.

Usage: python tools/extract_game_ult_charge.py
"""

import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from ow174.game.ult_charge import BUILTIN_TABLE
from ow174.paths import ROOT, ULT_CHARGE_OVERRIDE

PATH = ROOT / "data" / ULT_CHARGE_OVERRIDE


def main() -> None:
    path = PATH
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(BUILTIN_TABLE, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
