"""Scoped 0776 Soundwave direction queries from the Lucio movement analysis."""

import math

from ow174.game.mover import TURN, f32
from ow174.game.script import expr

HERO = 0x02E0000000000079
GRAPH = 0x0776
QUERIES = {"STU_2200A66D", "STU_021D01CD", "STU_0762B33A"}


def query(world, cfg, instance):
    cls = cfg.get("$")
    if (cls not in QUERIES or world.hero != HERO or instance is None or instance.graph.index != GRAPH
            or world.match is None or instance.stopped or instance.teardown):
        return NotImplemented
    player = next((actor for actor in world.match.players
                   if actor.has_body and actor.body == world.body
                   and actor.body_script is not None and not actor.body_script.retired
                   and actor.body_script.component is instance.component), None)
    if player is None:
        return NotImplemented
    if cls == "STU_0762B33A":
        return soundwave_direction(instance, cfg)
    target = cfg.get("m_entity") or {}
    if (target.get("$") != "STU_B5A0CAF0"
            or instance.evaluate(target) != expr.Entity(world.body)):
        return NotImplemented
    yaw = player.mover.yaw * TURN
    sine, cosine = f32(math.sin(yaw)), f32(math.cos(yaw))
    if cls == "STU_021D01CD":
        return expr.Vec3(sine, 0., cosine)
    pitch = player.mover.pitch * TURN
    horizontal = f32(math.cos(pitch))
    return expr.Vec3(f32(sine * horizontal), f32(-math.sin(pitch)), f32(cosine * horizontal))


def soundwave_direction(instance, cfg):
    """CEB820's actual0776 family: desired yaw/pitch around its forward+up basis.

    This computes the resource's vector only. It does not apply health knockback
    to another body, choose resistance, or implement the general limiter API.
    """
    yaw_limit = instance.evaluate(cfg.get("m_5023B9B0"))
    pitch_limit = instance.evaluate(cfg.get("m_E26E5E09"))
    if yaw_limit != 90 or pitch_limit != 25:
        return NotImplemented
    desired = instance.evaluate(cfg.get("m_32B00625"))
    basis = instance.evaluate(cfg.get("m_3661BC45"))
    return expr.limit_direction(desired, basis, yaw_limit, pitch_limit)
