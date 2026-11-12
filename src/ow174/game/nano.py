"""Selected resource-backed Ana Nano reconstruction for cross-hero acceptance.

Target ranking/cone/LOS and impact-time outgoing scaling are declared server
policies; native modifier/filter/stack/projectile snapshot services remain open.
"""

import math

from ow174.game import stats
from ow174.game.combat import EPSILON, _dot, _number, _sub, aim, ray_world
from ow174.game.script import expr


def offense(fight, source_body):
    source = fight.vitals.get(source_body)
    return 1.5 if source is not None and not source.dead and fight.now < source.nano_until else 1.0


def targets(self, body: int, state) -> tuple:
    """Reconstruct the base allied Nano search using live bodies and static LOS.

    Range40 comes from0B9E #1648. The base cone10 is from its target State;
    native ranking, zoom modifiers and engine filters are not claimed here.
    """
    shooter = next((player for player in self.match.players
                    if player.has_body and player.body == body), None)
    if (shooter is None or self.match.collision_world is None or self.match.game_map.free_for_all
            or self.is_dead(shooter) or self.is_stunned(shooter)):
        return ()
    radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0)
    if radius <= 0:
        return ()
    origin = self._eye(shooter)
    direction = aim(shooter.mover.yaw, shooter.mover.pitch)
    candidates = []
    for target in self._targets():
        if target.owner is shooter or target.team != shooter.team or self.vitals_of(target).dead:
            continue
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = _sub(centre, origin)
        distance = math.sqrt(_dot(delta, delta))
        if not EPSILON < distance <= radius:
            continue
        alignment = _dot(direction, delta) / distance
        if alignment < math.cos(math.radians(10)):
            continue
        wall = ray_world(self._collision(), origin, tuple(v / distance for v in delta), distance)
        if wall is not None and wall < distance - EPSILON:
            continue
        candidates.append((-alignment, distance, target.entity))
    return (expr.Entity(min(candidates)[2]),) if candidates else ()


def apply(self, body: int, targets, duration: float) -> bool:
    """Apply0B9E's released0269 effect to its selected living ally.

    B9F defines250 healing and additive+0.5 outgoing/-0.5 incoming damage.
    The authored source graph supplies duration8. This reconstructed target
    deadline survives caster death/retirement; native presentation is separate.
    """
    shooter = next((player for player in self.match.players
                    if player.has_body and player.body == body), None)
    if (shooter is None or self.match.game_map.free_for_all or self.is_dead(shooter)
            or self.is_stunned(shooter)
            or not math.isfinite(duration) or duration <= 0):
        return False
    targets = (targets,) if isinstance(targets, expr.Entity) else targets
    if type(targets) is not tuple:
        return False
    applied = False
    for target in self._targets():
        if (expr.Entity(target.entity) not in targets or target.owner is shooter
                or target.team != shooter.team):
            continue
        vitals = self.vitals_of(target)
        if vitals.dead:
            continue
        given = self._heal_pools(target.owner, vitals, 250.0)
        if given > 0:
            shooter.stats.add(stats.HEALING, given)
        vitals.nano_until = max(vitals.nano_until, self.now + duration)
        applied = True
    return applied
