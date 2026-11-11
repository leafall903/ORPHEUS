"""Scoped Match host for Moira's authored damage grasp and Coalescence."""

import math
from contextlib import contextmanager
from contextvars import ContextVar

from ow174.game import bots, dva_matrix
from ow174.game.commands import SECONDARY
from ow174.game.script import expr, filter_tags, runtime
from ow174.game.script.target_volume import Volume

HERO, WEAPON, ULTIMATE = 0x02E00000000001A2, 0x0F76, 0x1007
_grasp_frame = ContextVar("moira_grasp_frame", default=None)


@contextmanager
def grasp_command(fight, player, command):
    token = _grasp_frame.set((fight, player, command, {}))
    try:
        yield
    finally:
        _grasp_frame.reset(token)


def tracked_targets(world, search):
    if (world.hero != HERO or search.instance.component.strict
            or search.instance.graph.index != WEAPON or search.index != 106):
        return NotImplemented
    frame = _grasp_frame.get()
    if frame is None:
        return ()
    fight, player, command, contacts = frame
    if player.body_script is None or player.body_script.world is not world:
        return ()
    if search not in contacts:
        contacts[search] = _grasp_contact(fight, player, command, search)
    target, _ = contacts[search]
    return (target.entity,) if target is not None else ()


def _volume(search, origin, direction, margin=0.0):
    depth = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    aperture = expr.to_float(search.evaluate(search.node.field("m_A30B383E"), watch=False))
    parallel = bool(search.evaluate(search.node.field("m_E474FFD6"), watch=False))
    half = expr.f32(math.radians(aperture) * 0.5)
    cosine = expr.f32(math.cos(half))
    extent = expr.f32(expr.f32(math.tan(half)) * depth) if parallel else 0
    return Volume(origin, direction, (cosine, cosine), (extent, extent), (0, 0), (1, 1),
                  depth, margin=margin, parallel=parallel)


def _visible(collision, origin, target):
    from ow174.game.combat import EPSILON, _sub, ray_world

    x, y, z = target.feet
    for height in (0.3, 0.6):  # CA3570's generic capsule-height sample and fallback
        point = x, y + target.height * height, z
        length = math.dist(origin, point)
        if length <= EPSILON:
            return True
        direction = tuple(v / length for v in _sub(point, origin))
        if ray_world(collision, origin, direction, length) is None:
            return True
    return False


def _view_score(target, origin, forward, right, up):
    from ow174.game.combat import EPSILON, _dot, _sub

    x, y, z = _sub(target.feet, origin)
    bounds = []
    for height, side in ((0, -target.radius), (target.height, target.radius)):
        corner = (x, y + height, z)
        depth = _dot(corner, forward)
        if depth <= EPSILON:
            return None
        bounds.append(((_dot(corner, right) + side) / (2 * depth), _dot(corner, up) / (2 * depth)))
    horizontal, vertical = (max(0, min(a, b), -max(a, b)) for a, b in zip(*bounds, strict=True))
    return math.hypot(horizontal, vertical) * math.sqrt(2)


def _grasp_contact(fight, player, command, search):
    from ow174.game import sigma
    from ow174.game.combat import EPSILON, _sub, aim, ray_capsule, ray_world

    if (not command.buttons & SECONDARY or search.instance.component.disabled.get(29)
            or fight.is_dead(player) or fight.is_stunned(player)):
        return None, None
    if expr.to_int(search.evaluate(search.node.field("m_C39691D9"), watch=False)) <= 0:
        return None, None
    yaw, pitch = command.aim()
    origin, forward = fight._eye(player), aim(yaw, pitch)
    right, up = aim(yaw + 16384, 0), aim(yaw, pitch - 16384)
    margin = expr.to_float(search.evaluate(search.node.field("m_16BDE607"), watch=False))
    volume = _volume(search, origin, forward, margin)
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    collision = fight._collision()
    weights = []
    for term in search.node.field("m_84E5DE4E.m_503F0304") or ():
        config = term.get("m_84E5DE4E") or {}
        kind = config.get("$")
        lower = expr.to_float(search.evaluate(config.get("m_ABC68B33"), watch=False))
        upper = expr.to_float(search.evaluate(config.get("m_2AFC083F"), watch=False))
        if kind in ("STU_2FD4C482", "STU_AD7331B0") and upper > lower:
            weight = expr.to_float(search.evaluate(term.get("m_weight"), watch=False))
            weights.append((kind, lower, upper, weight))

    catalog = dva_matrix.tags()
    best = None
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None or not volume.capsule(shown.feet, shown.radius, shown.height)[0]:
            continue
        definition = target.owner.body if isinstance(target.owner, bots.Bot) else target.owner.hero.body
        tags = catalog.get(definition)
        if tags is not None:
            declared = filter_tags.Tags(base=tuple(tag & 0xFFFFFFFF for tag in tags))
            if filter_tags.evaluate(search.instance, search.node.field("m_filter"), declared) is False:
                continue
        if not _visible(collision, origin, shown):
            continue
        direction = forward
        a, b = shown.segment()
        distance = ray_capsule(origin, direction, a, b, shown.radius)
        if distance is None:
            closest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            delta = _sub(closest, origin)
            length = math.dist(closest, origin)
            direction = tuple(value / length for value in delta) if length > EPSILON else forward
            distance = max(0.0, length - shown.radius)
        score = 0.0
        for kind, lower, upper, weight in weights:
            value = (_view_score(shown, origin, forward, right, up)
                     if kind == "STU_2FD4C482" else math.dist(origin, shown.feet))
            score += 100 if value is None else weight * min(1, max(0, (value - lower) / (upper - lower)))
        rank = score, distance
        if best is None or rank < best[0]:
            best = rank, target, direction

    rank, target, direction = best if best is not None else ((0, volume.depth), None, forward)
    distance = rank[1]
    wall = ray_world(collision, origin, direction, distance)
    shield = sigma.barrier_intersection(fight, player, origin, direction, distance if wall is None else wall)
    if shield is not None and (wall is None or shield[0] < wall):
        return None, shield[1]
    return (target, None) if wall is None or wall >= distance else (None, None)


class _Recovery(runtime.InstanceContext):
    def native(self, cfg):
        if cfg.get("$") == "STU_4B02509E":
            return expr.f32(self.instance.component.frame_ms * expr.f32(.001))
        return super().native(cfg)


def _apply_grasp(fight, player, command):
    from ow174.game import sigma

    frame = _grasp_frame.get()
    if frame is None or frame[1] is not player:
        return
    for search, (target, shield) in frame[3].items():
        if not search.active or search.instance.component.disabled.get(29):
            continue
        instance = search.instance
        damage = instance.state(22)
        amount, loadout = _health_step(damage, player)
        if shield is not None:
            sigma.damage_barrier(fight, shield, amount)
            continue
        if target is None:
            continue
        damage_target = expr.to_entity(damage.evaluate(damage.node.field("m_FA3ECC43"), watch=False))
        if damage_target != target.entity:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(player, target, amount, False, loadout, command.frame)
        if vitals.total < before:
            node = instance.graph.node(48)
            value = expr.evaluate(node.field("m_value"), _Recovery(instance, None))
            instance.write(expr.lvalue(node.field("m_out_Var")), value)
            own = next((t for t in fight._targets() if t.owner is player), None)
            if own is not None:
                fight.heal_target(player, own, _health_step(instance.state(45), player)[0], credit=False)


def _health_step(state, player):
    modify = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False)) * player.quanta / 1e6
    loadout = state.evaluate(modify.get("m_loadout"), watch=False)
    return amount, loadout.guid if isinstance(loadout, expr.Asset) else None


def attack(fight, player, command, script):
    from ow174.game.combat import aim

    if script.hero != HERO or script.component.strict:
        return
    _apply_grasp(fight, player, command)
    instance = next((i for i in script.component.instances.values() if i.graph.index == ULTIMATE), None)
    if instance is None or 20 not in instance.states or not instance.states[20].active:
        return
    damage, loadout = _health_step(instance.states[20], player)
    healing, _ = _health_step(instance.state(18), player)
    self_heal = instance.state(12)
    origin, direction = fight._eye(player), aim(*command.aim())
    enemy_volume = _volume(instance.state(19), origin, direction)
    ally_volume = _volume(instance.state(17), origin, direction)
    enemies = {target.entity for target in fight._enemies(player)}
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    collision = fight._collision()
    for target in fight._targets():
        if target.owner is player:
            if self_heal.active:
                fight.heal_target(player, target, _health_step(self_heal, player)[0], credit=False)
            continue
        friendly = not fight.match.game_map.free_for_all and target.team == player.team
        if not friendly and target.entity not in enemies:
            continue
        shown = fight.seen(target, view_tick)
        volume = ally_volume if friendly else enemy_volume
        if (shown is None or not volume.capsule(shown.feet, shown.radius, shown.height)[0]
                or not _visible(collision, origin, shown)):
            continue
        if friendly:
            fight.heal_target(player, target, healing)
        else:
            fight._damage(player, target, damage, False, loadout, command.frame)
