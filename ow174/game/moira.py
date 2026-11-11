"""Combat services for Moira's authored F76 and 1007 beam graphs."""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from ow174.game import mei, phase_defenses, reinhardt_barrier
from ow174.game.commands import SECONDARY
from ow174.game.script import expr, runtime
from ow174.game.script.target_volume import Volume

HERO, GRASP, COALESCENCE = 0x02E00000000001A2, 0x0F76, 0x1007
COALESCENCE_LOADOUT = 0x0B9000000000014C


@dataclass
class PosedGrasp:
    player: object
    command: object
    contacts: dict = field(default_factory=dict)


_grasp_frame = ContextVar("moira_grasp_frame", default=None)


@contextmanager
def grasp_frame(player, command):
    token = _grasp_frame.set(PosedGrasp(player, command))
    try:
        yield
    finally:
        _grasp_frame.reset(token)


class RecoveryContext(runtime.InstanceContext):
    def native(self, cfg):
        if cfg.get("$") == "STU_4B02509E":
            # CF26E0 reads the health callback duration in milliseconds.
            return expr.f32(self.instance.component.frame_ms * expr.f32(.001))
        return super().native(cfg)


def _volume(state, origin, direction, *, margin=0.0):
    depth = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    aperture = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    parallel = expr.truthy(state.evaluate(state.node.field("m_E474FFD6"), watch=False))
    half = expr.f32(math.radians(aperture) * .5)
    cosine = expr.f32(math.cos(half))
    extent = expr.f32(expr.f32(math.tan(half)) * depth) if parallel else 0
    return Volume(origin, direction, (cosine, cosine), (extent, extent), (0, 0), (1, 1),
                  depth, margin=margin, parallel=parallel)


def _visible(fight, origin, target):
    from ow174.game.combat import capsule_visible

    x, y, z = target.feet
    line = fight._line_collision()
    for fraction in (.3, .6):
        point = x, y + target.height * fraction, z
        if capsule_visible(line, origin, point, point, 0):
            return True
    return False


def _projection(origin, forward, right, up, target):
    from ow174.game.combat import EPSILON, _dot, _sub

    x, y, z = _sub(target.feet, origin)
    bounds = []
    for height, side in ((0, -target.radius), (target.height, target.radius)):
        corner = x, y + height, z
        depth = _dot(corner, forward)
        if depth <= EPSILON:
            return 100.
        bounds.append(((_dot(corner, right) + side) / (2 * depth), _dot(corner, up) / (2 * depth)))
    horizontal, vertical = (max(0, min(a, b), -max(a, b)) for a, b in zip(*bounds, strict=True))
    return math.hypot(horizontal, vertical) * math.sqrt(2)


def _grasp_contact(fight, player, command, search):
    from ow174.game.combat import _sub, aim, ray_world

    if (fight.is_dead(player) or fight.is_stunned(player) or search.instance.component.disabled.get(29)
            or fight.match.collision_world is None or expr.to_int(
                search.evaluate(search.node.field("m_C39691D9"), watch=False), default=1,
            ) <= 0):
        return None, None, None
    origin = fight._eye(player)
    yaw, pitch = command.aim()
    direction = aim(yaw, pitch)
    right, up = aim(yaw + 16384, 0), aim(yaw, pitch - 16384)
    margin = expr.to_float(search.evaluate(search.node.field("m_16BDE607"), watch=False))
    volume = _volume(search, origin, direction, margin=margin)
    if volume.depth <= 0:
        return None, None, None
    terms = []
    for term in search.node.field("m_84E5DE4E.m_503F0304") or ():
        config = term.get("m_84E5DE4E") or {}
        kind = config.get("$")
        lower = expr.to_float(search.evaluate(config.get("m_ABC68B33"), watch=False))
        upper = expr.to_float(search.evaluate(config.get("m_2AFC083F"), watch=False))
        weight = expr.to_float(search.evaluate(term.get("m_weight"), watch=False))
        if kind in ("STU_2FD4C482", "STU_AD7331B0") and upper > lower:
            terms.append((kind, lower, upper, weight))
    best = None
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for target in fight._enemies(player):
        if target.is_device or mei.protected(target.owner) or not fight.visible_for_acquisition(
            target, player.mover.position,
        ):
            continue
        shown = fight.seen(target, view_tick)
        if (shown is None or not volume.capsule(shown.feet, shown.radius, shown.height)[0]
                or not _visible(fight, origin, shown)):
            continue
        distance = shown.ray(origin, direction, volume.depth)
        heading = direction
        if distance is None:
            lower, upper = shown.segment()
            nearest = lower[0], max(lower[1], min(upper[1], origin[1])), lower[2]
            offset = _sub(nearest, origin)
            length = math.hypot(*offset)
            if length > 0:
                heading = tuple(axis / length for axis in offset)
            distance = max(0.0, length - shown.radius)
        screen = _projection(origin, direction, right, up, shown)
        feet_distance = math.dist(origin, shown.feet)
        score = sum(weight * min(1, max(0, ((screen if kind == "STU_2FD4C482" else feet_distance)
                                           - lower) / (upper - lower)))
                    for kind, lower, upper, weight in terms)
        rank = score, distance, target.entity
        if best is None or rank < best[0]:
            best = rank, target, heading

    if best is None:
        target, reach = None, volume.depth
    else:
        (_, reach, _), target, direction = best
    wall = ray_world(fight._collision(), origin, direction, reach)
    limit = reach if wall is None else wall
    shield = reinhardt_barrier.ray_hit(fight, player, origin, direction, limit)
    pillar = fight.walls.cast(origin, direction, shield[0] if shield is not None else limit)
    for candidate in fight._enemies(player):
        if not mei.protected(candidate.owner):
            continue
        contact = candidate.ray(origin, direction, limit)
        if contact is not None and contact < min(
            shield[0] if shield else limit, pillar[0] if pillar else limit,
        ):
            return None, None, None
    if pillar is not None and (wall is None or pillar[0] < wall):
        return None, None, pillar[1]
    if shield is not None and (wall is None or shield[0] < wall):
        return None, shield[1], None
    return (target if wall is None else None), None, None


def grasp_targets(fight, player, search):
    frame = _grasp_frame.get()
    if frame is None or frame.player is not player:
        return ()
    if search not in frame.contacts:
        frame.contacts[search] = _grasp_contact(fight, player, frame.command, search)
    target, _, _ = frame.contacts[search]
    return (target.entity,) if target is not None else ()


def _health(state):
    from ow174.game.combat import hit_def

    return hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})


def grasp(fight, player, command):
    frame = _grasp_frame.get()
    if frame is None or frame.player is not player or not command.buttons & SECONDARY:
        return
    for search, (target, shield, pillar) in frame.contacts.items():
        instance = search.instance
        if not search.active or instance.component.disabled.get(29):
            continue
        damage = instance.state(22)
        definition = _health(damage)
        if definition.amount <= 0:
            continue
        step = instance.component.frame_ms / 1000
        amount = definition.amount * step
        if shield is not None:
            reinhardt_barrier.damage(shield, amount * fight.outgoing_scale(player.body), attacker=player.body)
            continue
        if pillar is not None:
            fight.walls.damage(pillar, player, amount * fight.outgoing_scale(player.body), command.frame)
            continue
        if target is None or expr.to_entity(
            damage.evaluate(damage.node.field("m_FA3ECC43"), watch=False),
        ) != target.entity:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(player, target, amount, False, definition.loadout, command.frame, tags=definition.tags)
        if vitals.total >= before:
            continue
        recovery = instance.graph.node(48)
        refill = expr.evaluate(recovery.field("m_value"), RecoveryContext(instance, None))
        instance.write(expr.lvalue(recovery.field("m_out_Var")), refill)
        own = fight.vitals.get(player.body)
        if own is None or own.missing_permanent <= 0:
            continue
        healing = _health(instance.state(45))
        owner = next((item for item in fight._targets() if item.owner is player), None)
        if owner is not None:
            fight.heal_target(owner, healing.amount * step, healer=player)


def beam_root(player):
    script = getattr(player, "body_script", None)
    if getattr(script, "hero", None) != HERO or script.retired or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == COALESCENCE), None)


@dataclass
class Beam:
    activation: tuple
    start: int
    end: int
    last_ms: int
    direction: tuple


class Coalescence:
    def __init__(self, fight):
        self.fight = fight
        self.beams = {}

    def retire(self, body):
        self.beams.pop(body, None)

    def interrupt(self, actor, body):
        self.retire(body)
        instance = beam_root(actor)
        scope = instance.states.get(1) if instance is not None else None
        if scope is not None and scope.active:
            with instance.component.operation():
                for index in (8, 2, 1):
                    state = instance.states.get(index)
                    if state is not None and state.active:
                        state.request(runtime.ABORT)
                instance.state(0).stop()
                instance.component.run_now()

    def observe(self, player, command):
        from ow174.game.combat import aim

        instance = beam_root(player)
        scope = instance.states.get(1) if instance is not None else None
        if scope is None or not scope.active:
            return
        activation = instance.id, scope.counter, scope.start
        direction = aim(*command.aim())
        beam = self.beams.get(player.body)
        if beam is not None and beam.activation == activation:
            beam.direction = direction
            return
        windup = instance.states.get(8)
        if windup is None or not windup.active:
            return
        chase = instance.state(2)
        duration = expr.to_float(chase.evaluate(chase.node.field("m_EB0FC261"), watch=False))
        if not math.isfinite(duration) or duration <= 0:
            return
        frame_ms = instance.component.frame_ms
        offset = windup.start - instance.component.frame * frame_ms
        start = self.fight.tick * frame_ms + offset + windup.ms
        self.beams[player.body] = Beam(activation, start, start + round(duration * 1000), start, direction)

    def replay_until(self, previous):
        return max((math.ceil(beam.end / mei.FRAME_MS) for beam in self.beams.values()), default=previous)

    def select(self, player, search, origin, direction):
        fight = self.fight
        volume = _volume(search, origin, direction)
        allies = search.index == 17
        for target in fight._targets():
            if target.owner is player or target.is_device or fight.vitals_of(target).dead:
                continue
            friendly = target.team == player.team and not fight.match.game_map.free_for_all
            if friendly != allies or (not friendly and (
                mei.protected(target.owner) or phase_defenses.active(target.owner)
            )):
                continue
            if (volume.capsule(target.feet, target.radius, target.height)[0]
                    and _visible(fight, origin, target)):
                yield target

    def track(self, player, search):
        from ow174.game.combat import aim

        return tuple(target.entity for target in self.select(
            player, search, self.fight._eye(player), aim(player.mover.yaw, player.mover.pitch),
        ))

    def update(self, tick):
        fight = self.fight
        now = tick * mei.FRAME_MS
        for player in fight.match.players:
            beam = self.beams.get(player.body)
            if beam is None:
                continue
            instance = beam_root(player)
            scope = instance.states.get(1) if instance is not None else None
            if scope is None or fight.is_dead(player):
                self.retire(player.body)
                continue
            if not scope.active and beam.end - beam.last_ms > 2 * mei.FRAME_MS:
                self.retire(player.body)
                continue
            # At natural expiry the command-frame chase can close before
            # the last fractional world interval. Account to the cast clock.
            until = min(now, beam.end)
            if until > beam.last_ms:
                elapsed = until - beam.last_ms
                beam.last_ms = until
                self.apply(player, instance, beam.direction, elapsed / 1000)
            if now >= beam.end:
                self.retire(player.body)

    def apply(self, player, instance, direction, elapsed):
        fight = self.fight
        origin = fight._eye(player)
        damage = _health(instance.state(20))
        healing = _health(instance.state(18))
        self_heal = _health(instance.state(12))
        owner = next((target for target in fight._targets() if target.owner is player), None)
        if owner is not None:
            fight.heal_target(owner, self_heal.amount * elapsed, healer=player)
        for target in self.select(player, instance.state(17), origin, direction):
            fight.heal_target(target, healing.amount * elapsed, healer=player)
        amount = damage.amount * elapsed * fight.outgoing_scale(player.body)
        for target in self.select(player, instance.state(19), origin, direction):
            if not reinhardt_barrier.protect_body(fight, target, amount, attacker=player.body):
                fight._damage(player, target, amount, False, damage.loadout, fight.tick,
                              tags=damage.tags, source_body=player.body, source_scaled=True,
                              world_tick=fight.tick)
