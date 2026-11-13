"""Echo replay queries body history without moving live recipients."""
from dataclasses import replace

PRIMARY = (0x19E5, 0x0400000000002CBF)
STICKY = (0x1C36, 0x0400000000002CBA)


def targets(fight, rocket, candidates):
    if ((rocket.source_graph, rocket.definition) not in (PRIMARY, STICKY)
            or rocket.shot is None or rocket.shot.clock_origin is None
            or fight.tick >= fight.match.tick):
        return candidates
    return [replace(shown, construct=target.construct) for target in candidates
            if (shown := fight.seen(target, fight.tick)) is not None]
