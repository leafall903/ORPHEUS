"""104319 projectile tag evidence for reconstructed Matrix interception."""

import hashlib
import json
import os
from functools import cache
from pathlib import Path

from ow174.game import pools

_tag_catalog = os.environ.get("ORPHEUS_DVA_PROJECTILE_TAGS")
TAG_CATALOG_SHA256 = (
    "ba348f1546a7384a1a5f4b3d3a88c98b1f28cc4da725161cf3360086215446d2" if _tag_catalog else None
)
GRAPH, STATE = 0x02CC, 3
EXCLUDED_TAG = "0x0D60000000000145"
# Independently checked 004A.003 XML. Keep shell support with just the mech supplements.
REAPER_SHELL = 0x040000000000004A


@cache
def _tags():
    if not _tag_catalog:
        return {REAPER_SHELL: ("0x0D600000000002F9",)}
    raw = Path(_tag_catalog).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TAG_CATALOG_SHA256:
        raise ValueError("D.Va projectile tag catalog checksum mismatch")
    data = json.loads(raw)
    return {int(key, 16): tuple(tags) for key, tags in data["entities"].items()}


def eligible(definition):
    """The authored mode2 filter is reconstructed as exclusion of tag0145."""
    tags = _tags().get(definition) if definition is not None else None
    return tags is not None and EXCLUDED_TAG not in tags


def active(actor):
    hero = actor.hero
    if hero.guid != pools.DVA_HERO or hero.body != pools.DVA_MECH:
        return False
    script = actor.body_script
    if script is None or script.retired or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            state = instance.states.get(STATE)
            return state is not None and state.active and not state.ending
    return False
