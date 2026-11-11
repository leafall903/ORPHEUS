"""D.Va Fusion Cannon pellet geometry, reconstructed from build104319 rings.

The authored graph supplies count, radii and spread. Degrees, tangent-plane
projection and zero ring phase are reconstruction choices, pending native
trajectory comparison. They must not be reported as verified client behavior.
"""

from ow174.game.shot_patterns import ring_directions

HERO = 0x02E000000000007A
MECH = 0x0400000000000407
FUSION_CANNONS = 0x050A


def pellet_directions(actor, state, direction):
    """Expand only D.Va's authored Fusion Cannon volley into independent rays."""
    if (getattr(getattr(actor, "hero", None), "guid", None) != HERO
            or state.instance.graph.index != FUSION_CANNONS):
        return (direction,)
    return ring_directions(state, direction)
