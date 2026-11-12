"""The Python packages of the Battle.net emulator, pinned in project metadata and installed on demand."""

import logging
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import tomllib

from ow174.launcher import LaunchError
from ow174.paths import PROJECT_FILE

log = logging.getLogger("ow174.launcher")


def _dependency_pins(project: Path) -> list[str]:
    if project.is_file():
        with project.open("rb") as handle:
            return tomllib.load(handle)["project"]["dependencies"]
    return [line for line in metadata.requires("orpheus") or () if ";" not in line]


def missing_requirements(project: Path = PROJECT_FILE) -> list[str]:
    """Direct dependency pins that are absent or have a different installed version."""
    missing = []
    for line in _dependency_pins(project):
        name, pinned, version = line.partition("==")
        try:
            if not pinned or metadata.version(name.strip()) != version.strip():
                missing.append(line)
        except metadata.PackageNotFoundError:
            missing.append(line)
    return missing


def ensure_requirements(project: Path = PROJECT_FILE) -> None:
    """Install what is missing with pip, so the network is only used on the first start."""
    missing = missing_requirements(project)
    if not missing:
        return
    log.info("Installing Python packages: %s", ", ".join(missing))
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-q",
        "--disable-pip-version-check",
        *missing,
    ]
    if subprocess.run(command).returncode != 0:
        raise LaunchError(
            "Could not install the Python packages. Check the internet connection and start again."
        )
