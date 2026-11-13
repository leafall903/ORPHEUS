"""Qualified recipient services for original0D07; not a helper entry router."""

from ow174.game import defenses
from ow174.game.script import expr


def _recipient(world, instance):
    match = getattr(world, 'match', None)
    component = instance.component
    if (match is None or component.world is not world or component.strict
            or component.instances.get(instance.id) is not instance
            or instance.stopped or instance.teardown or component.owner_dead
            or component.fault is not None
            or not (component.entity == getattr(world, 'body', None)
                    or getattr(world, 'component', None) is component)):
        return None
    owner = next((p for p in match.players if p.body == component.entity
                  and p.has_body and not p.gone and p.body_script is not None
                  and not p.body_script.retired and p.body_script.component is component), None)
    if owner is None:
        owner = next((b for b in match.bots if b.entity == component.entity and not b.dead
                      and b.body_script is not None and b.body_script.component is component), None)
    if owner is None or match.combat.is_dead(owner):
        return None
    return owner


def response(world, cfg, instance):
    """Read the original helper's self modifier26 using the existing host policy."""
    if (instance is None or instance.graph.index != 0x0D07
            or cfg.get('$') != 'STU_ABE57332'
            or (cfg.get('m_entity') or {}).get('$') != 'STU_B5A0CAF0'):
        return NotImplemented
    owner = _recipient(world, instance)
    if owner is None:
        return expr.EVALUATION_FAILED
    return expr.f32(defenses.knockback_scale(owner))


def native(world, cfg, instance):
    value = response(world, cfg, instance)
    if value is not NotImplemented:
        return value
    if (instance is None or instance.graph.index != 0x0D07
            or cfg.get('$') != 'STU_309B0570' or cfg.get('m_5B94EDB8') is not None
            or (cfg.get('m_entity') or {}).get('$') != 'STU_B5A0CAF0'):
        return NotImplemented
    owner = _recipient(world, instance)
    if owner is None:
        return expr.EVALUATION_FAILED
    from ow174.game import body_script_requests, bots

    # Ordinary NPCs need an already admitted sole mover. A query cannot create it.
    mover = (body_script_requests.npc_mover(world.match, owner) if isinstance(owner, bots.Bot)
             else world.match._script_body_mover(instance.component.entity, instance.component.entity))
    if mover is None:
        return expr.EVALUATION_FAILED
    return expr.Vec3(*map(expr.f32, mover.velocity))
