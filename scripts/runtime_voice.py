"""Choose voice metadata locales advertised by the active retail installation."""

import csv
import locale
from pathlib import Path
import re
import sys


VOICE_CATALOGS = ("player_voice_174.json", "voice_effects_174.json")


def user_locale():
    if sys.platform == "win32":
        import ctypes
        return locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultLCID(), "")
    return locale.getlocale()[0] or ""


def voice_locales(install: Path, version: str, preferred: str):
    with (install / ".build.info").open(encoding="utf-8-sig", newline="") as stream:
        tags = [row.get("Tags!STRING:0", "") for row in csv.DictReader(stream, delimiter="|")
                if row.get("Active!DEC:1") == "1" and row.get("Product!STRING:0", "pro") == "pro"
                and row.get("Version!STRING:0") == version]
    options = {"text": [], "speech": []}
    for value in tags:
        for code, kind in re.findall(r"\b([a-z]{2}[A-Z]{2})\s+(text|speech)\??\b", value):
            if code not in options[kind]:
                options[kind].append(code)
    if not all(options.values()):
        raise ValueError("No active build.info text/speech locale pair for local voice extraction")
    preferred = preferred.replace("-", "").replace("_", "").casefold()
    def rank(code):
        if code.casefold() == preferred:
            return 0
        if code[:2].casefold() == preferred[:2]:
            return 1
        return 2
    # Match speech first. A text-only or speech-only language may require different manifests.
    speech = min(options["speech"], key=rank)
    text = speech if speech in options["text"] else min(options["text"], key=rank)
    return text, speech
