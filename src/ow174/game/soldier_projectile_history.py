"""Replay the qualified ordinary Helix flight against retained physical poses."""

from dataclasses import replace

from ow174.game.content import SOLDIER

HELIX = (0x0254, 0x040000000000072C)


def _qualified(rocket):
    return ((rocket.source_graph, rocket.definition) == HELIX
            and getattr(getattr(rocket.shooter, 'hero', None), 'guid', None) == SOLDIER
            and rocket.shot is not None and rocket.shot.body == rocket.source_entity
            and type(rocket.launch_tick) is int and type(rocket.last_tick) is int)


def advance(fight, rocket):
    if not _qualified(rocket):
        return False
    from ow174.game.projectile_clock import advance_accepted

    advance_accepted(fight, rocket)
    return True


def targets(fight, rocket, candidates):
    if not _qualified(rocket) or fight.tick >= fight.match.tick:
        return candidates
    return [replace(shown, construct=target.construct) for target in candidates
            if (shown := fight.seen(target, fight.tick)) is not None]
