"""Server-side expansion of the manual Enforcer's authored projectile rings."""

import math

from ow174.game.npc_context import ENFORCER
from ow174.game.script import expr


def manual(bot):
    return bot.hero == ENFORCER and bot.spawner.get("manual_spawn") is True


def pellet_directions(bot, state, forward):
    node = state.node
    if not manual(bot) or (state.instance.graph.index, node.pos) != (0x13EB, 24):
        return ()
    launch = node.field("m_5EC1EB38")
    if not launch or launch.get("$") != "STU_613BDDB0":
        return ()
    spread = expr.to_float(state.evaluate(launch["m_spread"], watch=False))
    count = expr.to_int(state.evaluate(node.field("m_numProjectilesPerShot"), watch=False))
    rings = [(expr.to_float(state.evaluate(row["m_6425FFB9"], watch=False)),
              expr.to_int(state.evaluate(row["m_B19BD35D"], watch=False)))
             for row in launch.get("m_0D30E40C") or ()]
    if (count != 25 or [size for _, size in rings] != [1, 8, 16] or rings[0][0] != 0
            or not math.isfinite(spread) or not 0 <= spread < 90
            or not all(0 < scale <= 1 for scale, _ in rings[1:])):
        return ()
    # Reconstruct the angular scale and azimuth. The graph supplies counts,
    # relative ring radii and spread but the native orientation is unverified.
    yaw = math.tau * round(bot.yaw) / 65536
    right = (math.cos(yaw), 0.0, -math.sin(yaw))
    x, y, z = forward
    up = (-y * math.sin(yaw), z * right[0] - x * right[2], -y * math.cos(yaw))
    directions = []
    for scale, size in rings[1:]:
        angle = math.radians(spread * scale)
        axial, radial = math.cos(angle), math.sin(angle)
        for index in range(size):
            azimuth = math.tau * index / size
            r, u = radial * math.cos(azimuth), radial * math.sin(azimuth)
            directions.append(tuple(a * axial + b * r + c * u
                                    for a, b, c in zip(forward, right, up, strict=True)))
    return directions
