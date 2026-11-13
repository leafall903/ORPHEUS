"""Supported live-body bindings for the 104319 position getter family.

71A87E92/CCC750 selects an entity, then asks the world for its pose position.
21A13145/CE47D0 instead uses D1A410's eye/pose service. Its full attachment,
snapshot and height rules are not reconstructed here: only the existing own
upright-player melee eye profile is admitted. No controller/parent traversal
or owner-frame-to-position conversion is introduced.
"""

from ow174.game.script import expr


def native(world, cfg, instance):
    match = getattr(world, 'match', None)
    if match is None or instance is None:
        return NotImplemented
    cls = cfg.get('$')
    if cls == 'STU_71A87E92':
        entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
        return match.script_body_position(entity, world.body)
    if cls == 'STU_21A13145' and instance.graph.index == 0x0043:
        entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
        if entity == world.body:
            owner = match.script_player_body(entity, world.body)
            if owner is not None:
                return expr.Vec3(*match.combat._eye(owner))
        return None
    return NotImplemented
