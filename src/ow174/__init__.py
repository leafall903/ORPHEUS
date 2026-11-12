"""Compatibility for running the Overwatch 1.74 package from a source checkout."""

from pathlib import Path

__path__.append(str(Path(__file__).resolve().parents[1] / "src" / "ow174"))
