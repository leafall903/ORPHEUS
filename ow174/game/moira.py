"""Moira F76 Grasp using authored operations and reconstructed target acquisition."""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from ow174.game import sigma_hyperspheres
from ow174.game.script import expr, runtime
from ow174.game.script.target_volume import Volume

HERO = 0x02E00000000001A2
GRASP = 0x0F76
DAMAGE_SEARCH = 106
COALESCENCE = 0x1007
BEAM_SEARCHES = (17, 19)


class _RecoveryContext(runtime.InstanceContext):
    """Explicit per-command step for the missing damage-callback context.

    4B02509E's compiled getter CF26E0 reads context+10's int64+430 and multiplies
    by f32(.001). This adapter declares that duration as this posed script step;
    native health notification cadence remains a separate acceptance question.
    """

    def native(self, cfg):
        if cfg.get("$") == "STU_4B02509E":
            return expr.f32(self.instance.component.frame_ms * expr.f32(0.001))
        return super().native(cfg)


def _recover_grasp(instance):
    # F76/node48 is authored min(maxFuel, fuel + 3 * stepSeconds / .016).
    # Execute its expression rather than duplicating rates or resetting fuel.
    node = instance.graph.node(48)
    value = expr.evaluate(node.field("m_value"), _RecoveryContext(instance, None))
    instance.write(expr.lvalue(node.field("m_out_Var")), value)


def _grasp_projection(origin, forward, right, up, target):
    from ow174.game import combat

    """Score the historical capsule's projected bounds in the command view."""
    x, y, z = combat._sub(target.feet, origin)
    bounds = []
    # The server has an upright capsule and a symmetric 90-degree view.
    for height, side in ((0, -target.radius), (target.height, target.radius)):
        corner = (x, y + height, z)
        depth = combat._dot(corner, forward)
        if depth <= combat.EPSILON:
            return None
        bounds.append(((combat._dot(corner, right) + side) / (2 * depth),
                       combat._dot(corner, up) / (2 * depth)))
    horizontal, vertical = (max(0, min(a, b), -max(a, b)) for a, b in zip(*bounds, strict=True))
    return math.hypot(horizontal, vertical) * math.sqrt(2)


@dataclass
class BioticFrame:
    """Share one posed command's targets between TrackTargets and combat."""
    fight: object
    player: object
    command: object
    contacts: dict = field(default_factory=dict)
    beam_targets: dict = field(default_factory=dict)

    def targets(self, search):
        if search.instance.graph.index == COALESCENCE:
            if search not in self.beam_targets:
                self.beam_targets[search] = _coalescence_targets(
                    self.fight, self.player, self.command, search,
                )
            return tuple(target.entity for target in self.beam_targets[search])
        if search not in self.contacts:
            self.contacts[search] = _grasp_contact(self.fight, self.player, self.command, search)
        target, _, _ = self.contacts[search]
        return (target.entity,) if target is not None else ()


_biotic_frame = ContextVar("moira_biotic_frame", default=None)


@contextmanager
def biotic_command(fight, player, command):
    frame = BioticFrame(fight, player, command)
    token = _biotic_frame.set(frame)
    try:
        yield
    finally:
        _biotic_frame.reset(token)


def tracked_targets(world, search):
    frame = _biotic_frame.get()
    script = frame.player.body_script if frame is not None else None
    return frame.targets(search) if script is not None and script.world is world else ()


def _grasp_contact(fight, player, command, search):
    from ow174.game import combat

    if (not command.buttons & 2 or search.instance.component.disabled.get(29)
            or fight.match.collision_world is None or fight.is_dead(player) or fight.is_stunned(player)):
        return None, None, None
    # CAF449/CAFC26: a resolved nonpositive cap leaves no selected targets.
    cap = search.evaluate(search.node.field("m_C39691D9"), watch=False)
    if cap is not None and expr.to_int(cap, default=0x7FFFFFFF) <= 0:
        return None, None, None
    yaw, pitch = command.aim()
    origin, direction = fight._eye(player), combat.aim(yaw, pitch)
    right = combat.aim(yaw + 16384, 0)
    up = combat.aim(yaw, pitch - 16384)
    margin = combat._number(search.evaluate(search.node.field("m_16BDE607"), watch=False), 0)
    volume = _track_volume(search, origin, direction, margin=margin)
    reach = volume.depth
    terms = []
    for term in search.node.field("m_84E5DE4E.m_503F0304") or ():
        config = term.get("m_84E5DE4E") or {}
        kind = config.get("$")
        if kind not in ("STU_2FD4C482", "STU_AD7331B0"):
            continue
        weight = combat._number(search.evaluate(term.get("m_weight"), watch=False), 0)
        lower = combat._number(search.evaluate(config.get("m_ABC68B33"), watch=False), 0)
        upper = combat._number(search.evaluate(config.get("m_2AFC083F"), watch=False), 0)
        if upper > lower:
            terms.append((kind, weight, lower, upper))
    collision = fight._collision()
    best = None
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None or not volume.capsule(shown.feet, shown.radius, shown.height)[0]:
            continue
        if not _track_visible(fight, collision, origin, shown):
            continue
        segment = shown.segment()
        contact_direction = direction
        distance = combat.ray_capsule(origin, direction, *segment, shown.radius)
        if distance is None:
            # The search margin can admit a body outside the center ray.
            # Trace to its unexpanded capsule for cover and barrier contact.
            lower, upper = segment
            nearest = (lower[0], max(lower[1], min(upper[1], origin[1])), lower[2])
            offset = combat._sub(nearest, origin)
            length = math.sqrt(combat._dot(offset, offset))
            contact_direction = tuple(axis / length for axis in offset) if length else direction
            distance = max(0.0, length - shown.radius)
        score = 0.0
        for kind, weight, lower, upper in terms:
            if kind == "STU_2FD4C482":
                value = _grasp_projection(origin, direction, right, up, shown)
                if value is None:
                    score += 100
                    continue
            else:
                value = math.dist(origin, shown.feet)
            score += weight * min(1, max(0, (value - lower) / (upper - lower)))
        rank = (score, distance)
        if best is None or rank < best[0]:
            best = rank, target, contact_direction

    rank, target, direction = best if best is not None else ((0, reach), None, direction)
    distance = rank[1]
    wall = combat.ray_world(collision, origin, direction, distance)
    pillar = fight.walls.cast(origin, direction, distance)
    if pillar is not None and (wall is None or pillar[0] < wall):
        wall = pillar[0]
    else:
        pillar = None
    limit = min(distance, wall) if wall is not None else distance
    shield = sigma_hyperspheres.barrier_contact(fight, player, origin, direction, limit)
    if shield is not None and (wall is None or shield[0] < wall):
        return None, shield, None
    if pillar is not None:
        return None, None, pillar
    if target is None or (wall is not None and wall < distance):
        return None, None, None
    return target, None, None


def _track_volume(search, origin, direction, *, margin=0.0):
    from ow174.game import combat

    depth = combat._number(search.evaluate(search.node.field("m_radius"), watch=False), 0)
    aperture = combat._number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0)
    parallel = search.evaluate(search.node.field("m_E474FFD6"), watch=False)
    half_angle = expr.f32(math.radians(aperture) * 0.5)
    outer_cos = expr.f32(math.cos(half_angle))
    extent = expr.f32(expr.f32(math.tan(half_angle)) * depth) if parallel else 0
    return Volume(origin, direction, (outer_cos, outer_cos), (extent, extent),
                  (0, 0), (1, 1), depth, margin=margin, parallel=bool(parallel))


def _track_visible(fight, collision, origin, target):
    from ow174.game import combat

    # CA3570's default point and its fallback when hardpoint 01CD is unavailable.
    # The host supplies the historical upright capsule and static world queries.
    x, y, z = target.feet
    for fraction in (0.3, 0.6):
        offset = combat._sub((x, y + target.height * fraction, z), origin)
        distance = math.sqrt(combat._dot(offset, offset))
        if distance <= combat.EPSILON:
            return True
        direction = tuple(axis / distance for axis in offset)
        if (combat.ray_world(collision, origin, direction, distance) is None
                and fight.walls.cast(origin, direction, distance) is None):
            return True
    return False


def grasp(fight, player, command):
    from ow174.game import combat

    frame = _biotic_frame.get()
    if frame is None or frame.player is not player:
        return
    for search, (target, shield, pillar) in frame.contacts.items():
        if not search.active or search.instance.component.disabled.get(29):
            continue
        instance = search.instance
        damage = instance.state(22)
        definition = combat.hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
        if definition is None or definition.amount <= 0:
            continue
        amount = definition.amount * (player.quanta / 1e6)
        if shield is not None:
            _, apply, args, _ = shield
            apply(*args, amount * fight.offense_scale(player))
            continue
        if pillar is not None:
            fight.walls.damage(pillar[1], player, amount * fight.offense_scale(player))
            continue
        damage_target = expr.to_entity(damage.evaluate(damage.node.field("m_FA3ECC43"), watch=False))
        if target is None or damage_target != target.entity:
            continue
        dealt = fight._damage(player, target, amount, False, definition.loadout, command.frame,
                              tags=definition.tags, beam=True)
        if dealt <= 0:
            continue
        _recover_grasp(instance)
        healing = instance.state(45)
        operation = combat.hit_def(healing, {"m_modifyHealth": healing.node.field("m_modifyHealth")})
        own = fight.vitals.get(player.body)
        if own is not None and operation is not None:
            fight._settle_heal(player, player, own, operation.amount * (player.quanta / 1e6))


@dataclass
class Spray:
    radius: float
    hits: set[int] = field(default_factory=set)


@dataclass
class SprayHeal:
    healer: object
    team: int
    begins: float
    expires: float
    accounted: float


def spray_trace(shooter, state, hit):
    if (getattr(getattr(shooter, "hero", None), "guid", None) != HERO
            or state.instance.graph.index != GRASP or state.index != 6 or hit is None):
        return None
    config = state.node.field("m_6396149F") or {}
    if (config.get("m_modifyHealth") or {}).get("m_93CDFD2F", 1) != 0:
        return None
    radius = expr.to_float(state.evaluate(config.get("m_71A4B759"), watch=False))
    return Spray(max(0.0, radius))


def fly_spray(fight, rocket, duration):
    from ow174.game import combat

    packet = rocket.spray
    world = fight._collision()
    origin, direction = rocket.position, rocket.direction
    distance = rocket.speed * duration
    wall = combat.ray_world(world, origin, direction, distance)
    pillar = fight.walls.cast(origin, direction, distance)
    stop = min(distance, wall) if wall is not None else distance
    if pillar is not None:
        stop = min(stop, pillar[0])
    shield = sigma_hyperspheres.barrier_contact(fight, rocket.shooter, origin, direction, stop,
                                               radius=packet.radius)
    if shield is not None:
        stop = min(stop, shield[0])
    if not fight.match.game_map.free_for_all:
        for target in fight._targets():
            if (target.owner is rocket.shooter or target.team != rocket.team
                    or target.entity in packet.hits or fight.vitals_of(target).dead):
                continue
            a, b = target.segment()
            at = combat.ray_capsule(origin, direction, a, b, target.radius + packet.radius)
            if at is None or at > stop:
                continue
            point = combat._along(origin, direction, at)
            if not combat.capsule_visible(world, point, a, b, target.radius, walls=fight.walls):
                continue
            packet.hits.add(target.entity)
            fight._heal(rocket.shooter, target, rocket.direct.amount)
            # 10B3 trailing heal refreshes rather than stacking.
            begins = fight.now + 0.2
            fight.moira_hots[target.entity] = SprayHeal(
                rocket.shooter, rocket.team, begins, begins + 2.0, begins,
            )
    rocket.position = combat._along(origin, direction, stop)
    elapsed = stop / rocket.speed
    rocket.flown += elapsed
    rocket.left = max(0.0, rocket.left - elapsed)
    if wall is not None or pillar is not None or shield is not None or rocket.left <= combat.EPSILON:
        fight._landed(rocket)


def advance_spray_heals(fight):
    if not fight.moira_hots:
        return
    targets = {target.entity: target for target in fight._targets()}
    for entity, effect in tuple(fight.moira_hots.items()):
        target = targets.get(entity)
        if (target is None or fight.vitals_of(target).dead or fight.match.game_map.free_for_all
                or target.team != effect.team):
            del fight.moira_hots[entity]
            continue
        until = min(fight.now, effect.expires)
        if until > effect.accounted:
            fight._heal(effect.healer, target, (until - effect.accounted) * 17.5)
            effect.accounted = until
        if fight.now >= effect.expires:
            del fight.moira_hots[entity]


def _coalescence_targets(fight, player, command, search):
    from ow174.game import combat

    if fight.match.collision_world is None:
        return ()
    origin, direction = fight._eye(player), combat.aim(*command.aim())
    volume = _track_volume(search, origin, direction)
    world = fight._collision()
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    healing = search.index == 17
    targets = []
    for target in fight._targets():
        if target.owner is player or fight.vitals_of(target).dead:
            continue
        friendly = not fight.match.game_map.free_for_all and target.team == player.team
        if friendly != healing:
            continue
        shown = fight.seen(target, view_tick)
        if (shown is not None and volume.capsule(shown.feet, shown.radius, shown.height)[0]
                and _track_visible(fight, world, origin, shown)):
            targets.append(target)
    return tuple(targets)


def coalescence(fight, player, script, command):
    from ow174.game import combat

    frame = _biotic_frame.get()
    if frame is None or frame.player is not player:
        return
    seconds = player.quanta / 1e6
    for search, targets in frame.beam_targets.items():
        healing = search.index == 17
        state = search.instance.states.get(18 if healing else 20)
        if not search.active or state is None or not state.active:
            continue
        definition = combat.hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        if definition is None:
            continue
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if not isinstance(selected, tuple):
            continue
        amount = definition.amount * seconds
        for target in targets:
            if expr.Entity(target.entity) not in selected:
                continue
            if healing:
                fight._heal(player, target, amount)
            else:
                fight._damage(player, target, amount, False, definition.loadout,
                              command.frame, tags=definition.tags, beam=True)
    for instance in script.component.instances.values():
        if instance.graph.index != COALESCENCE:
            continue
        state = instance.states.get(12)
        if state is None or not state.active:
            continue
        definition = combat.hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        own = fight.vitals.get(player.body)
        if own is not None and definition is not None:
            fight._settle_heal(player, player, own, definition.amount * seconds)
