"""Locate the locally built relay for the Windows retail client."""

from pathlib import Path

from ow174.launcher import LaunchError
from ow174.paths import RELAY_DLL


def ensure_relay_dll(path: Path = RELAY_DLL) -> Path:
    if path.is_file():
        return path
    raise LaunchError(
        f"Missing Windows retail relay at {path}. Run relay\\build.bat "
        "from an x64 Native Tools Command Prompt for Visual Studio, then start again."
    )
