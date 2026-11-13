"""Bounded actual0263 actions and0776 direction queries; full movement remains open.

Native D2B350 supplies next-frame keys and dispatches the two row types. The
self-body query paths use C5E040 look, D1B5E0 yaw basis and C63D60 velocity.
Only the live human owner's yaw-only, stationary-world mover is supported.
"""

import math
from dataclasses import replace

from ow174.game import velocity
from ow174.game.mover import ADD, SET, TURN, f32
from ow174.game.script import expr

HERO = 0x02E0000000000079
GRAPH = 0x0263
SOUNDWAVE_GRAPH = 0x0776
QUERIES = {"STU_2200A66D", "STU_021D01CD", "STU_66F3B89E", "STU_309B0570", "STU_A2A35433"}
SOUNDWAVE_QUERIES = {"STU_2200A66D", "STU_021D01CD", "STU_0762B33A"}


def owner(world, instance):
    component = instance.component if instance is not None else None
    if (getattr(world, "hero", None) != HERO or world.match is None
            or component is None or instance.graph.index not in (GRAPH, SOUNDWAVE_GRAPH)
            or component.entity != world.body or instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance):
        return None
    return next((player for player in world.match.players
                 if player.has_body and player.body == world.body
                 and player.body_script is not None and not player.body_script.retired
                 and player.body_script.component is component), None)


def query(world, cfg, instance):
    cls = cfg.get("$")
    if instance is None or getattr(world, "hero", None) != HERO:
        return NotImplemented
    supported = (QUERIES if instance.graph.index == GRAPH else
                 SOUNDWAVE_QUERIES if instance.graph.index == SOUNDWAVE_GRAPH else ())
    if cls not in supported:
        return NotImplemented
    player = owner(world, instance)
    if cls == "STU_0762B33A":
        if player is None:
            return expr.EVALUATION_FAILED
        return soundwave_direction(instance, cfg)
    target = cfg.get("m_entity") or {}
    if (target.get("$") != "STU_B5A0CAF0"
            or instance.evaluate(target) != expr.Entity(world.body)):
        return NotImplemented
    if cls == "STU_A2A35433":  # CCF4A0 ->1B8D940: raw held list, not Ability.disabled
        if player is None:
            return expr.EVALUATION_FAILED
        button = expr.to_int(instance.evaluate(cfg.get("m_logicalButton")))
        return button in instance.component.held
    if player is None:
        return NotImplemented
    mover = player.mover
    if cls == "STU_309B0570":
        # The true relative/platform option is a distinct unimplemented path.
        if cfg.get("m_5B94EDB8") is not None:
            return NotImplemented
        return expr.Vec3(*mover.velocity)
    yaw = mover.yaw * TURN
    sine, cosine = f32(math.sin(yaw)), f32(math.cos(yaw))
    if cls == "STU_021D01CD":
        return expr.Vec3(sine, 0., cosine)
    if cls == "STU_66F3B89E":
        return expr.Vec3(cosine, 0., -sine)  # native cross(up, forward)
    pitch = mover.pitch * TURN
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
    if (not isinstance(desired, expr.Vec3) or not isinstance(basis, expr.Vec3)
            or not all(map(math.isfinite, (*desired, *basis)))):
        return NotImplemented
    angles = []
    for value in (desired, basis):
        square = f32(sum(f32(axis * axis) for axis in value))
        if square < expr.EPSILON:
            return expr.EVALUATION_FAILED
        length = math.sqrt(square)
        x, y, z = (f32(axis / length) for axis in value)
        angles.append((math.atan2(x, z), math.asin(max(-1., min(1., -y)))))
    result = []
    for wanted, centre, limit in zip(angles[0], angles[1], (yaw_limit, pitch_limit), strict=True):
        difference = (wanted - centre + math.pi) % math.tau - math.pi
        limit = math.radians(limit)
        result.append(centre + max(-limit, min(limit, difference)))
    yaw, pitch = result
    horizontal = math.cos(pitch)
    return expr.Vec3(f32(math.sin(yaw) * horizontal), f32(-math.sin(pitch)),
                     f32(math.cos(yaw) * horizontal))


def action(instance, node):
    """Prepare both actual row types before writing the owner's pending queue.

    Unknown rows/priorities defer to the VM's existing unsupported policy.
    A known missing mover still allows the generic output, as D2B350 does.
    """
    world = instance.component.world
    if (getattr(world, "hero", None) != HERO or instance.graph.index != GRAPH
            or not hasattr(world, "match") or world.match is None):
        return NotImplemented
    player = owner(world, instance)
    if player is None or world.match.combat.is_dead(player) or world.match.combat.is_stunned(player):
        return True
    rows = node.field("m_BC5E91CF")
    if (not isinstance(rows, list) or len(rows) != 2
            or not 0 <= node.rid <= 0xFFFF or not 0 <= instance.id <= 0xFFFF):
        return NotImplemented
    for row, cls, field, index in zip(rows, ("STU_9AF0FA80", "STU_4635C5E2"),
                                    ("m_A9561CA0", "m_CFEC5F76"), (3, 1), strict=True):
        if (not isinstance(row, dict) or row.get("$") != cls or row.get(field) != index
                or row.get("m_priority") is not None):
            return NotImplemented
    frame = instance.component.frame + 1
    if frame > 0xFFFFFFFF:
        return NotImplemented  # a new command epoch needs its own body lifecycle
    scalar = instance.evaluate(rows[0].get("m_9B7A63EA"))
    vector = instance.evaluate(rows[1].get("m_9B7A63EA"))
    if (type(scalar) not in (int, float) or not math.isfinite(scalar)
            or not isinstance(vector, expr.Vec3) or not all(map(math.isfinite, vector))):
        return NotImplemented
    key = (instance.id << 48) | (node.rid << 32) | ((frame & 0xFFFF) << 16)
    changes = (velocity.numeric(frame, 3, SET, scalar),
               velocity.vector(frame, ADD, tuple(vector), player.mover.velocity))
    for index, change in enumerate(changes):
        velocity.enqueue(player.mover.velocity_changes, replace(change, key=key | index))
    return True
