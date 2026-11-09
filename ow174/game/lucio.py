"""Resource-backed Soundwave with reconstructed cone and impulse."""

import math

from ow174.game.script import expr, runtime
from ow174.game.script.combat_pose import active_pose

HERO = 0x02E0000000000079


class SoundwaveImpulseContext(runtime.InstanceContext):
    def native(self, cfg):
        if cfg.get("$") == "STU_0762B33A":
            # The reconstructed cone supplies a unit factor for selected targets.
            return 1.0
        return super().native(cfg)


def modify_health(world, state, elapsed):
    from ow174.game.combat import _dot, _sub, aim

    if state.instance.graph.index != 0x0776 or state.node.pos != 30:
        return NotImplemented
    pose = active_pose.get()
    if elapsed or pose is None or pose[0] is not world:
        return True
    _, player, command = pose
    if state.instance.component.frame != command.frame:
        return True
    fight = world.match.combat
    if world.match.collision_world is None or fight.is_dead(player) or fight.is_stunned(player):
        return True
    node = state.instance.graph.node(29)
    reach = expr.to_float(state.evaluate(node.field("m_radius"), watch=False))
    cosine = math.cos(math.radians(expr.to_float(state.evaluate(node.field("m_A30B383E"), watch=False))/2))
    origin, direction = fight._eye(player), aim(*command.aim())
    horizontal = math.hypot(direction[0], direction[2])
    selected = []
    for target in fight._enemies(player):
        point = (target.feet[0], target.feet[1]+target.eye, target.feet[2])
        delta = _sub(point, origin)
        distance = math.sqrt(_dot(delta, delta))
        if (0 < distance <= reach and _dot(direction, delta) >= cosine*distance
                and fight.area_visible(player, origin, target)):
            selected.append(target)
    state.instance.write(expr.dynamic(node.field("m_8A2905A0")),
                         tuple(expr.Entity(target.entity) for target in selected))
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    loadout = state.evaluate(settings["m_loadout"], watch=False)
    context = SoundwaveImpulseContext(state.instance, None)
    impulse = expr.to_float(expr.evaluate(settings["m_1376B4DF"], context))
    for target in selected:
        fight._damage(player, target, amount, False, loadout.guid, command.frame)
        mover = getattr(target.owner, "mover", None)
        if (mover is not None and horizontal > 1e-6 and math.isfinite(impulse)
                and impulse > 0 and not fight.vitals_of(target).dead):
            # Vertical launch is reconstructed and follows the authored horizontal scale.
            vx, vy, vz = mover.velocity
            mover.velocity = (vx+impulse*direction[0]/horizontal, max(vy, impulse*4/17),
                              vz+impulse*direction[2]/horizontal)
    return True
