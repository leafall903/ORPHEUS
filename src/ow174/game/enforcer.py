"""Actual13EB ring scatter; seed identities/body muzzle remain server mappings."""
import math

from ow174.game import weapon_spread
from ow174.game.script import expr

HERO, GRAPH, NODE = 0x02E00000000001CE, 0x13EB, 24


def directions(shooter, state, number, activation, forward):
    if shooter.hero != HERO or state.instance.graph.index != GRAPH or state.node.pos != NODE:
        return NotImplemented
    launch = state.node.field('m_5EC1EB38')
    if not isinstance(launch, dict) or launch.get('$') != 'STU_613BDDB0':
        raise ValueError('unsupported Enforcer launch shape')

    def scalar(config, default=0.0):
        value = default if config is None else expr.to_float(state.evaluate(config, watch=False))
        if not math.isfinite(value):
            raise ValueError('nonfinite ring launch parameter')
        return expr.f32(value)

    rings = tuple((scalar(row.get('m_6425FFB9')),
                   expr.to_int(state.evaluate(row.get('m_B19BD35D'), watch=False)))
                  for row in launch.get('m_0D30E40C') or ())
    projectiles = expr.to_int(state.evaluate(state.node.field('m_numProjectilesPerShot'), watch=False))
    if (not rings or any(radius < 0 or count <= 0 for radius, count in rings)
            or sum(count for _, count in rings) != projectiles):
        raise ValueError('ring rows must cover every authored projectile')
    aspect = scalar(launch.get('m_6425FFB9'), 1.0)
    if aspect != 1.0:
        raise ValueError('noncircular Enforcer spread is not restored')
    cone = scalar(launch.get('m_spread'))
    jitter = scalar(launch.get('m_92308789'))
    phase_cfg = launch.get('m_391F15AF')
    phase = weapon_spread.ring_phase(None if phase_cfg is None else scalar(phase_cfg))
    # D5EB70 inputs are native owner/instance/state words and a counter. This
    # explicit server mapping is deterministic; native handle/client parity is open.
    base = weapon_spread.seed_base(shooter.entity, state.instance.id, state.node.pos, activation[0])
    return tuple(weapon_spread.ring_direction(forward, cone, jitter, phase, rings, base, number, pellet)
                 for pellet in range(projectiles))
