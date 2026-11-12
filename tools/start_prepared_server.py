"""Apply prepared integration inputs before invoking the existing server CLI."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare_arguments(profile_path, arguments):
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise ValueError("prepared server inputs require manifest version 1")
    environment = profile.get("environment")
    options = profile.get("server_cli_arguments", [])
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError("prepared server environment must contain string names and values")
    if not isinstance(options, list) or any(not isinstance(value, str) for value in options):
        raise ValueError("prepared server CLI arguments must be strings")
    os.environ.update(environment)
    return [*options, *arguments]


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments:
        raise SystemExit("Usage: start_prepared_server.py ENVIRONMENT.json [existing server options]")
    prepared = prepare_arguments(arguments[0], arguments[1:])
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    # The existing modules select immutable runtime inputs during import.
    from ow174.cli import main as server_main

    return server_main(prepared)


if __name__ == "__main__":
    raise SystemExit(main())
