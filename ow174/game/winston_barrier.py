"""Host sphere for Winston's authored 00FA Barrier Projector."""

import math
from dataclasses import dataclass

from ow174.game import bots, winston_jump
from ow174.game.script import expr, nodes

RADIUS = 5.0  # D02.00C MSTU sphere used by 01C3.003.
GRAPH = 0x00FA


@dataclass
class PendingRelease:
    owner: object
    wait: nodes.Wait
    generation: int
    frame: int
    tick: int

    def live(self, fight):
        script = self.owner.body_script
        return (not fight.match.ended and not self.owner.gone and self.owner.has_body
                and script is not None and not script.retired
                and script.component is self.wait.instance.component and script.frame < self.frame
                and self.wait.active and self.wait.counter == self.generation
                and not fight.is_dead(self.owner))


def observe_cast(fight, owner, script):
    if script.hero != winston_jump.HERO or fight.match.ended:
        return
    from ow174.game.combat import TICK
    from ow174.game.projectiles import MAX_LEASE_FRAME

    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    wait = instance.states.get(3) if instance is not None else None
    if not isinstance(wait, nodes.Wait) or not wait.active:
        return
    pending = fight.reinhardt_barriers.winston_pending
    previous = pending.get(owner.body)
    if previous is not None and previous.wait is wait and previous.generation == wait.counter:
        return
    frame = (wait.start + wait.ms) // script.frame_ms
    if not script.frame < frame < MAX_LEASE_FRAME:
        return
    elapsed = (frame - script.frame) * script.frame_ms
    tick = fight.match.tick + math.ceil(elapsed / (TICK * 1000))
    pending[owner.body] = PendingRelease(owner, wait, wait.counter, frame, tick)


def clock_edges(fight, now, tick):
    """Keep skipped projectile sweeps on their own side of the shell's lifetime."""
    from ow174.game.combat import TICK

    service = fight.reinhardt_barriers
    edges = set()
    for release in service.winston_pending.values():
        if release.live(fight):
            edges.update((release.tick, release.tick + 1))
            duration = expr.to_float(release.wait.instance.component.entity_var(567).value())
            if duration > 0:
                expiry = release.tick + math.ceil(duration / TICK - 1e-9)
                edges.update((expiry - 1, expiry))
    for actor in service.winston.values():
        expiry = tick + math.ceil((actor.expires - now) / TICK - 1e-9)
        edges.update((actor.starts_tick - 1, actor.starts_tick, expiry - 1, expiry))
    return {edge for edge in edges if fight.tick < edge <= tick}


def advance_releases(fight):
    pending = fight.reinhardt_barriers.winston_pending
    for body, release in tuple(pending.items()):
        if not release.live(fight):
            pending.pop(body, None)
        elif fight.tick > release.tick:
            # Leave the release frame available for real input until the next world tick.
            release.owner._advance_body_gap(release.frame + 1)
            pending.pop(body, None)


def intersection(centre, origin, direction, reach, *, radius=0.0):
    """Intersect a shell at its entry or exit, including a finite query radius."""
    offset = tuple(a - b for a, b in zip(origin, centre, strict=True))
    projection = sum(a * b for a, b in zip(offset, direction, strict=True))
    squared = sum(value * value for value in offset)
    side = squared - RADIUS * RADIUS
    extent = max(0.0, RADIUS - radius) if side < -1e-8 else RADIUS + radius
    distance = squared - extent * extent
    discriminant = projection * projection - distance
    if discriminant < 0:
        return None
    root = math.sqrt(discriminant)
    if side < -1e-8:
        contact = -projection + root
    else:
        contact = -projection - root
        if contact <= 1e-8 and projection >= 0:
            return None
    return max(0.0, contact) if contact <= reach else None


@dataclass
class Bubble:
    entity: int
    owner: object
    instance: object
    team: int
    centre: tuple
    health: float
    expires: float
    starts_tick: int

    def retire(self):
        if (not self.instance.stopped and self.owner.body == self.instance.component.entity
                and self.instance.find_var(0, 38).value() == expr.Entity(self.entity)):
            self.instance.write((0, 38), expr.Entity(0))

    def intersection(self, origin, direction, reach, *, radius=0.0):
        return intersection(self.centre, origin, direction, reach, radius=radius)

    def nearest(self, point):
        distance = math.dist(self.centre, point)
        if distance <= 1e-9:
            x, y, z = self.centre
            return RADIUS, (x + RADIUS, y, z)
        surface = tuple(a + (b - a) * RADIUS / distance
                        for a, b in zip(self.centre, point, strict=True))
        return abs(distance - RADIUS), surface

    def overlaps(self, a, b, radius, visible):
        from ow174.game.combat import _segment_point

        near = _segment_point(self.centre, a, b)
        distance = math.dist(self.centre, near)
        far = max(math.dist(self.centre, a), math.dist(self.centre, b))
        if distance > RADIUS + radius or far < RADIUS - radius:
            return False
        direction = tuple(end - start for start, end in zip(a, b, strict=True))
        length = math.hypot(*direction)
        if length:
            hit = self.intersection(a, tuple(value / length for value in direction), length)
            if hit is not None:
                point = tuple(start + value * hit / length for start, value in zip(a, direction, strict=True))
                return visible(point)
        point = near if distance >= RADIUS else max((a, b), key=lambda p: math.dist(self.centre, p))
        return visible(self.nearest(point)[1])


def deploy(instance, node):
    world = instance.component.world
    if (instance.component.strict or instance.graph.index != GRAPH or node.pos != 26
            or getattr(world, "hero", None) != winston_jump.HERO
            or getattr(world, "match", None) is None
            or instance.evaluate(node.field("m_entityDefinition")) != expr.Asset(0x04000000000001C3)):
        return NotImplemented
    fight = world.match.combat
    owner = next((p for p in fight.match.players if p.has_body and p.body == world.body), None)
    if fight.match.ended or owner is None or owner.gone or fight.is_dead(owner) or fight.is_stunned(owner):
        return False
    health = expr.to_float(instance.find_var(0, 587).value())
    duration = expr.to_float(instance.component.entity_var(567).value())
    if health <= 0 or duration <= 0:
        return False
    entity = fight.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Barrier Projector entity allocator exhausted")
    fight.next_bot += 1
    x, y, z = owner.mover.position
    deployed = fight.now
    starts_tick = fight.tick
    pending = fight.reinhardt_barriers.winston_pending.pop(owner.body, None)
    if (pending is not None and pending.wait.instance is instance
            and pending.generation == pending.wait.counter):
        from ow174.game.combat import TICK

        deployed += (pending.tick - fight.tick) * TICK
        if fight.tick < pending.tick:
            # The release command follows the combat contacts of its world frame.
            starts_tick = pending.tick + 1
    fight.reinhardt_barriers.winston[entity] = Bubble(
        entity, owner, instance, owner.team, (x, y + 0.1, z), health, deployed + duration, starts_tick,
    )
    instance.write(expr.lvalue(node.field("m_B3019338")), expr.Entity(entity))
    return True
