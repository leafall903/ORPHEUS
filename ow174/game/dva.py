"""D.Va Fusion Cannon pellet geometry, reconstructed from build104319 rings.

The authored graph supplies count, radii and spread. Degrees, tangent-plane
projection and zero ring phase are reconstruction choices, pending native
trajectory comparison. They must not be reported as verified client behavior.
"""

from ow174.game.pellets import ring_directions

HERO = 0x02E000000000007A
BOOSTERS = 0x047E
FUSION_CANNONS = 0x050A
LIGHT_GUN = 0x02B8
LIGHT_GUN_PROJECTILE = 0x0400000000001000
MICRO_MISSILE = 0x0400000000001E8A  # Authored 1128 volley projectile.


def boosters(actor):
    script = actor.body_script
    if script is None or script.hero != HERO:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == BOOSTERS), None)


def pellet_directions(actor, state, direction):
    """Expand only D.Va's authored Fusion Cannon volley into independent rays."""
    if (getattr(getattr(actor, "hero", None), "guid", None) != HERO
            or state.instance.graph.index != FUSION_CANNONS):
        return (direction,)
    return ring_directions(state, direction)
