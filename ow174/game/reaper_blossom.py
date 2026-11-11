"""Static world damage for Reaper's original 0044 Death Blossom channel."""

import math
from dataclasses import dataclass, field

from ow174.game import contacts, mei, mei_wall, reinhardt_barrier
from ow174.game.script import expr

HERO = 0x02E0000000000002
GRAPH = 0x0044
SHOTGUN = 0x0019
LOADOUT = 0x0B90000000000010
DAMAGE, PERIOD_MS = 34.0, 200  # 0044 effect4 -> 0045
DAMAGE_TAGS = tuple(0x0D80000000000000 | tag for tag in (0x6FA, 0x1FD, 0x9A6, 0x2D2E, 0x4D2A))
REAPING = 0x0075
REAPING_RATIO = 0.35000002  # 0075 state5


def reaping(shooter, fight, target, dealt, source):
    script = getattr(shooter, "body_script", None)
    if (getattr(script, "hero", None) != HERO or script.retired or script.component.owner_dead
            or shooter.body != source or target.owner is shooter
            or (target.team == shooter.team and not fight.match.game_map.free_for_all)
            or not any(i.graph.index == REAPING for i in script.component.instances.values())):
        return
    vitals = fight.vitals.get(shooter.body)
    if vitals is None or vitals.dead or vitals.missing_permanent <= 0:
        return
    owner = next((item for item in fight._targets() if item.owner is shooter), None)
    if owner is not None:
        fight.heal_target(owner, dealt * REAPING_RATIO, healer=shooter)


def wait_state(player):
    script = getattr(player, "body_script", None)
    if getattr(script, "hero", None) != HERO or script.retired or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            state = instance.states.get(3)
            if state is not None and state.active:
                return state
    return None


@dataclass
class Channel:
    activation: tuple
    ends: int
    radius: float
    due: dict = field(default_factory=dict)


class Blossom:
    def __init__(self, fight):
        self.fight = fight
        self.channels = {}

    def retire(self, body):
        self.channels.pop(body, None)

    def interrupt(self, player, body):
        self.retire(body)
        state = wait_state(player)
        if state is not None:
            state.request_finish()
            state.instance.component.run_now()

    def observe(self, player):
        state = wait_state(player)
        if state is None:
            self.retire(player.body)
            return
        activation = state.instance.id, state.counter, state.start
        current = self.channels.get(player.body)
        if current is not None and current.activation == activation:
            return
        tracker = state.instance.state(5)
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        duration = expr.to_float(state.evaluate(state.node.field("m_timeout"), watch=False))
        if not all(math.isfinite(n) and n > 0 for n in (radius, duration)):
            return
        now = self.fight.tick * mei.FRAME_MS
        end = now - max(0, state.now - state.start) + round(duration * 1000)
        current = self.channels[player.body] = Channel(activation, end, radius)
        self.pulse(player, current, now)

    def replay_until(self, previous):
        return max((math.ceil(c.ends / mei.FRAME_MS) for c in self.channels.values()), default=previous)

    def update(self, tick):
        now = tick * mei.FRAME_MS
        for player in self.fight.match.players:
            channel = self.channels.get(player.body)
            if channel is None:
                continue
            if self.fight.is_dead(player) or wait_state(player) is None:
                self.retire(player.body)
            elif now < channel.ends:
                self.pulse(player, channel, now)
            else:
                channel.due.clear()

    def pulse(self, player, channel, now):
        from ow174.game.combat import _sub, capsule_visible, segment_distance

        if now >= channel.ends:
            return
        fight = self.fight
        x, y, z = player.mover.position
        origin = x, y + 1.8, z  # 0044's authored tracker offset
        collision = fight._collision()
        # Resolve each recipient before applying damage. A pulse that breaks a
        # shield or pillar cannot also damage the body it covered.
        available = {}
        for target in fight._enemies(player):
            a, b = target.segment()
            if (mei.protected(target.owner)
                    or segment_distance(origin, a, b) - target.radius > channel.radius):
                continue
            point = target.feet[0], (a[1] + b[1]) / 2, target.feet[2]
            delta = _sub(point, origin)
            distance = math.hypot(*delta)
            if not capsule_visible(collision, origin, point, point, 0):
                continue
            direction = tuple(v / distance for v in delta) if distance else (0., 0., 1.)
            shield = reinhardt_barrier.ray_hit(
                fight, player, origin, direction, min(distance, channel.radius), include_bubbles=False,
            )
            pillar = fight.walls.cast(origin, direction, shield[0] if shield else distance)
            if pillar is not None or shield is not None:
                available[target.entity] = (None, None, None)
                if shield is not None and pillar is None:
                    barrier = shield[1]
                    available[barrier.entity] = (None, barrier, None)
            else:
                available[target.entity] = (target, None, None)
        for piece in fight.walls.pieces.values():
            point = mei_wall.closest(piece, origin)
            distance = math.dist(origin, point)
            if distance > channel.radius or not capsule_visible(collision, origin, point, point, 0):
                continue
            if distance:
                direction = tuple(v / distance for v in _sub(point, origin))
                pillar = fight.walls.cast(origin, direction, distance)
                shield = reinhardt_barrier.ray_hit(fight, player, origin, direction, distance,
                                                   include_bubbles=False)
                if shield is not None or (pillar is not None and pillar[1] is not piece):
                    continue
            available[piece.entity] = (None, None, piece)
        for shield in reinhardt_barrier.enemy_barriers(fight, player):
            if shield.entity in available or not shield.mesh:
                continue
            anchor, axes = reinhardt_barrier.pose(shield)
            local = tuple(sum((origin[k] - anchor[k]) * axis[k] for k in range(3)) for axis in axes)
            nearest = min((contacts.closest_on_triangle(*tri, local) for tri in shield.mesh),
                          key=lambda point: math.dist(point, local))
            if math.dist(nearest, local) > channel.radius:
                continue
            point = tuple(anchor[k] + sum(nearest[i] * axes[i][k] for i in range(3)) for k in range(3))
            if capsule_visible(fight._line_collision(), origin, point, point, 0):
                available[shield.entity] = (None, shield, None)
        damage = DAMAGE * fight.outgoing_scale(player.body)
        channel.due = {entity: channel.due.get(entity, now) for entity in available}
        for entity, (target, shield, piece) in available.items():
            due = channel.due[entity]
            if due > now:
                continue
            channel.due[entity] = due + PERIOD_MS
            if shield is not None:
                reinhardt_barrier.damage(shield, damage, attacker=player.body)
            elif piece is not None:
                fight.walls.damage(piece, player, damage, fight.tick)
            elif target is not None and not reinhardt_barrier.protect_body(
                fight, target, damage, attacker=player.body,
            ):
                fight._damage(player, target, damage, False, LOADOUT, fight.tick,
                              world_tick=fight.tick, tags=DAMAGE_TAGS, source_scaled=True)
