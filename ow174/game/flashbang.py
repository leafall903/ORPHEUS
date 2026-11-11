"""Build 104319 launch and gravity arithmetic for Cassidy's owned 01E9/7."""

from ow174.game import weapon_spread
from ow174.game.script import expr


def launch(shooter, state, forward):
    if (shooter.hero.guid != 0x02E0000000000042 or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x01E9 or state.node.pos != 7):
        return None
    motions = state.node.field("m_projectileMotions") or ()
    if (len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579"
            or motions[0].get("m_D0C4CF49") is not None):
        return None
    cfg = motions[0].get("m_599AB8D7")
    if cfg is None:
        return None
    gravity = expr.f32(expr.to_float(state.evaluate(cfg, watch=False)))
    lift_cfg = (state.node.field("m_5EC1EB38") or {}).get("m_22CE7F30")
    lift = expr.to_float(state.evaluate(lift_cfg, watch=False)) if lift_cfg is not None else 0.0
    return gravity, weapon_spread.launch_direction(forward, lift)


def accelerate(velocity, gravity, seconds):
    """D71ACD-D71ADE: apply the unconditional float32 Y velocity change."""
    x, y, z = velocity
    return x, expr.f32(expr.f32(y) - expr.f32(expr.f32(gravity) * expr.f32(seconds))), z
