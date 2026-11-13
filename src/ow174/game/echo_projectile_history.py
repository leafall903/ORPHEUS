"""Echo Tri-Shot replay queries body history without moving live recipients."""
from dataclasses import replace

PRIMARY = (0x19E5, 0x0400000000002CBF)


def targets(fight, rocket, candidates):
    if ((rocket.source_graph, rocket.definition) != PRIMARY
            or rocket.shot is None or rocket.shot.clock_origin is None
            or fight.tick >= fight.match.tick):
        return candidates
    return [replace(shown, construct=target.construct) for target in candidates
            if (shown := fight.seen(target, fight.tick)) is not None]
