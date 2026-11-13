"""Source14 query over registered Match body capsules.

The normal host supplies this query; original TrackTargets owns publication.
Paired output, normal16 helper routing and health completion remain separate.
No accepted legacy contact, damage result, copied force or recipient clock is
an input. This adapter covers ordinary live player/NPC bodies; native factories,
arbitrary tags, parented shapes and deployables require their own bindings.
"""

import math

from ow174.game import bots, defenses, doomfist
from ow174.game.script import expr, nodes
from ow174.game.script.target_volume import Volume


def tracked_targets(world, state):
    """Return source14 IDs, NotImplemented outside this explicit body contract."""
    instance, component = state.instance, state.instance.component
    match = world.match
    if ((world.hero, instance.graph.index, state.node.pos) != (doomfist.HERO, 0xD06, 14)
            or match is None or component.entity != world.body
            or component.frame != match.tick
            or component.world is not world or component.owner_dead or component.fault
            or instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance
            or not isinstance(state, nodes.TrackTargets) or not state.active):
        return NotImplemented
    source = match.script_player_body(world.body, world.body)
    if (source is None or source.body_script is None or source.body_script.retired
            or source.body_script.component is not component or match.combat.is_dead(source)):
        return NotImplemented
    node = state.node
    centre = state.evaluate(node.field('m_05BE62DD'), watch=False)
    sight = state.evaluate(node.field('m_D59188D5'), watch=False)
    direction = match.script_body_facing(world.body, world.body, pitched=True)
    radius = state.evaluate(node.field('m_radius'), watch=False)
    aperture = state.evaluate(node.field('m_A30B383E'), watch=False)
    horizontal = state.evaluate(node.field('m_B9BEAC7F'), watch=False)
    vertical = state.evaluate(node.field('m_3119AD61'), watch=False)
    yaw_offset = state.evaluate(node.field('m_D45E22FA'), watch=False)
    limit = state.evaluate(node.field('m_C39691D9'), watch=False)
    if (not all(nodes._finite_vector(v) for v in (centre, sight, direction))
            or not all(nodes._finite_number(v) for v in (radius, aperture, vertical, yaw_offset, limit))
            or radius <= 0 or yaw_offset != 0 or horizontal is not None
            or node.field('m_43478CAD') != 2
            or state.evaluate(node.field('m_E474FFD6'), watch=False) is not True):
        return NotImplemented
    # C9B9AB..C9BBE6: absent horizontal multiplier defaults1; authored vertical
    # multiplier1.21 scales its own angle/extent. Reuse C8E660 capsule math.
    half = expr.f32(expr.f32(aperture * expr.f32(math.pi / 180)) * .5)
    extent = expr.f32(expr.f32(math.tan(half)) * radius)
    angles = (half, expr.f32(half * vertical))
    volume = Volume(tuple(centre), tuple(direction),
                    tuple(expr.f32(math.cos(v)) for v in angles),
                    (extent, expr.f32(extent * vertical)), (0, 0), (1, 1), radius,
                    parallel=True)
    fight = match.combat
    collision = fight._collision()
    if collision is None:
        return NotImplemented
    cryo = defenses.cryo_blockers(match.players, exclude=source)
    found = []
    for target in fight._enemies(source):
        owner = target.owner
        if isinstance(owner, bots.Bot):
            if owner.dead or owner not in match.bots or owner.entity != target.entity:
                continue
        elif (owner not in match.players or owner.gone or not owner.has_body
              or owner.body != target.entity):
            continue
        script = owner.body_script
        recipient = getattr(script, 'component', None)
        if (recipient is None or getattr(script, 'retired', False)
                or world._body_component(target.entity) is not recipient
                or recipient.entity != target.entity or recipient.owner_dead or recipient.fault
                or getattr(recipient.world, 'match', None) is not match):
            continue
        if (volume.capsule(target.feet, target.radius, target.height)[0]
                and doomfist._two_height_visible(target, tuple(sight), collision, cryo)):
            found.append(target)
    # Native mode2 ranks resolved feet; limit and stable equal-distance order
    # apply after geometry/LOS. Never reinterpret m24A4AB8A as Meteor distances.
    found.sort(key=lambda target: math.dist(source.mover.position, target.feet))
    return tuple(target.entity for target in found[:max(0, int(limit))])
