"""Authored Self-Destruct and Call Mech radial combat services."""

import math

from ow174.game import bots
from ow174.game.script import entity_queries, expr, runtime

HERO = 0x02E000000000007A
MECH = 0x0400000000000407
GRAPH = 0x0B2F
LANDING = 0x026A


def source_for(world, state, pos):
    component = state.instance.component
    site = state.instance.graph.index, state.node.pos
    if world.hero != HERO or world.match is None or component.strict:
        return None
    if site == (LANDING, {8: 35, 22: 36}[pos]):
        return next((p for p in world.match.players if world.match._shown(p) and p.body == component.entity
                     and p.hero.guid == HERO and p.body_script is not None
                     and p.body_script.component is component and not component.owner_dead), None)
    if site != (GRAPH, pos):
        return None
    for bot in world.match.bots:
        if (bot.entity == component.entity and bot.body == MECH and bot.detached_bomb
                and bot.body_script is not None and bot.body_script.component is component):
            return bot
    return next((p for p in world.match.players if p.has_body and p.body == component.entity
                 and p.hero.body == MECH and p.body_script is not None
                 and p.body_script.component is component), None)


class BlastDistance(runtime.InstanceContext):
    def __init__(self, instance, distance):
        super().__init__(instance, None)
        self.distance = expr.f32(distance)

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 680):
            return self.distance
        return super().var(scope, var)


class ImpulseContext(runtime.InstanceContext):
    def native(self, cfg):
        result = super().native(cfg)
        if cfg.get("$") == entity_queries.POSSESSED and not expr.valid_entity(expr.to_entity(result)):
            linked = cfg.get("m_entity") or {}
            owner = linked.get("m_D63CBEC2") or {}
            if linked.get("$") == "STU_F76C486C" and owner.get("$") == "STU_B5A0CAF0":
                return expr.Entity(self.instance.component.entity)
        return result


def targets(world, state):
    from ow174.game.combat import capsule_visible, segment_distance

    source = source_for(world, state, 8)
    if source is None:
        return False
    fight = world.match.combat
    instance = state.instance
    node = state.node
    offset = state.evaluate(node.field("m_05BE62DD"), watch=False)
    reach = expr.to_float(state.evaluate(node.field("m_radius"), watch=False))
    if not isinstance(offset, expr.Vec3) or not math.isfinite(reach) or reach <= 0:
        return False
    blast = instance.graph.index == GRAPH
    if blast:
        feet = source.position if isinstance(source, bots.Bot) else source.mover.position
        origin = tuple(a + b for a, b in zip(feet, offset, strict=True))
    else:
        origin = tuple(offset)
    owner = source.owner if isinstance(source, bots.Bot) else source.entity
    rows, shields = [], {}
    for target in fight._enemies(source):
        if isinstance(target.owner, bots.Bot) and target.owner.owner == owner:
            continue
        if isinstance(source, bots.Bot) and target.owner.entity == owner:
            continue
        a, b = target.segment()
        distance = max(0.0, segment_distance(origin, a, b) - target.radius)
        point = (a[0], min(max(origin[1], a[1]), b[1]), a[2])
        if (distance > reach or fight.walls.blocked(origin, point)
                or not capsule_visible(fight._collision(), origin, a, b, target.radius)):
            continue
        delta = tuple(x - y for x, y in zip(point, origin, strict=True))
        length = math.hypot(*delta)
        if distance > 0 and length > 0:
            direction = tuple(axis / length for axis in delta)
            contact = fight._shield_hit(source, origin, direction, distance)
            if contact is not None:
                hit, shield = contact
                key = id(getattr(shield, "instance", getattr(shield, "shield", shield)))
                if key not in shields or hit < shields[key][0]:
                    shields[key] = (hit, shield)
                continue
        rows.append((target.entity, expr.f32(distance)))
    instance.write(expr.lvalue(node.field("m_8A2905A0")), tuple(expr.Entity(e) for e, _ in rows))
    if blast:
        instance.write(expr.lvalue(node.field("m_CEE20F6F")), tuple(distance for _, distance in rows))
        damage = instance.graph.node(24).field("m_value")
    else:
        damage = instance.graph.node(36).field("m_modifyHealth.m_amount")
    for distance, shield in shields.values():
        shield.damage(expr.to_float(expr.evaluate(damage, BlastDistance(instance, distance))))
    return True


def damage(world, state, elapsed):
    source = source_for(world, state, 22)
    if source is None:
        return NotImplemented
    if elapsed:
        return True
    fight = world.match.combat
    node = state.node
    selected = state.evaluate(node.field("m_FA3ECC43"), watch=False)
    selected = selected if isinstance(selected, tuple) else (selected,)
    ids = {entity.id for entity in selected if isinstance(entity, expr.Entity)}
    amount = expr.to_float(state.evaluate(node.field("m_modifyHealth.m_amount"), watch=False))
    asset = state.evaluate(node.field("m_modifyHealth.m_loadout"), watch=False)
    loadout = asset.guid if isinstance(asset, expr.Asset) else None
    origin = state.evaluate(node.field("m_modifyHealth.m_C0139FA6"), watch=False)
    for target in fight._enemies(source):
        if target.entity not in ids:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(source, target, amount, False, loadout, fight.tick)
        mover = getattr(target.owner, "mover", None)
        if (vitals.total < before and not vitals.dead and mover is not None
                and isinstance(origin, expr.Vec3) and not fight.control_immune(target.owner)
                and not fight.captured(target.entity)):
            _impulse(source, target, mover, state, tuple(origin))
    state.request_finish()
    return True


def _impulse(source, target, mover, state, origin):
    from ow174.game.combat import segment_distance

    distance = segment_distance(origin, *target.segment()) - target.radius
    rings = state.node.field("m_modifyHealth.m_knockbackRings") or ()
    context = ImpulseContext(state.instance, None)
    horizontal = expr.to_float(expr.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), context))
    vertical = expr.to_float(expr.evaluate(state.node.field("m_modifyHealth.m_583C208B"), context))
    for ring in rings:
        if distance > expr.to_float(state.evaluate(ring.get("m_radius"), watch=False)):
            continue
        planar = expr.to_float(state.evaluate(ring.get("m_9102DA3F"), watch=False))
        lift = expr.to_float(state.evaluate(ring.get("m_10399AE7"), watch=False))
        _radial_impulse(source, target, mover, origin, horizontal * planar, vertical * lift)
        return


def _radial_impulse(source, target, mover, origin, horizontal, vertical):
    from ow174.game.combat import aim

    if not (horizontal or vertical):
        return
    dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
    length = math.hypot(dx, dz)
    if length:
        dx, dz = dx / length, dz / length
    else:
        dx, _, dz = aim(source.yaw if isinstance(source, bots.Bot) else source.mover.yaw, 0)
    mover.add_external_impulse((dx * horizontal, vertical, dz * horizontal))


def booster_contact(fight, mech, frame):
    from ow174.game.combat import _along, aim, capsule_visible, segment_distance

    instance = next((i for i in mech.body_script.component.instances.values()
                     if i.graph.index == 0x047E), None)
    track = instance.states.get(6) if instance is not None else None
    modify = instance.states.get(7) if instance is not None else None
    if track is None or modify is None or not (track.active and modify.active):
        mech.booster_contact = None
        return
    offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
    amount = expr.to_float(modify.evaluate(modify.node.field("m_modifyHealth.m_amount"), watch=False))
    if (not isinstance(offset, expr.Vec3) or not math.isfinite(radius) or radius <= 0
            or not math.isfinite(amount) or amount <= 0):
        return
    asset = modify.evaluate(modify.node.field("m_modifyHealth.m_loadout"), watch=False)
    loadout = asset.guid if isinstance(asset, expr.Asset) else None
    yaw, pitch = mech.yaw, mech.pitch
    centre = _along(mech.position, aim(yaw - 16384, 0), offset.x)
    centre = _along(centre, aim(yaw, pitch - 16384), offset.y)
    centre = _along(centre, aim(yaw, pitch), offset.z)
    activation = instance.id, modify.counter
    if mech.booster_contact is None or mech.booster_contact[:2] != activation:
        mech.booster_contact = (*activation, set())
    hit = mech.booster_contact[2]
    origin = fight._eye(mech)
    for target in fight._enemies(mech):
        if target.entity in hit:
            continue
        a, b = target.segment()
        point = (a[0], min(max(origin[1], a[1]), b[1]), a[2])
        if (segment_distance(centre, a, b) > radius + target.radius
                or fight.walls.blocked(origin, point)
                or not capsule_visible(fight._collision(), origin, a, b, target.radius)):
            continue
        hit.add(target.entity)
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(mech, target, amount, False, loadout, frame)
        mover = getattr(target.owner, "mover", None)
        if (vitals.total < before and not vitals.dead and mover is not None
                and not fight.control_immune(target.owner) and not fight.captured(target.entity)):
            context = ImpulseContext(instance, None)
            horizontal = expr.to_float(expr.evaluate(modify.node.field("m_modifyHealth.m_E33EFEE1"), context))
            vertical = expr.to_float(expr.evaluate(modify.node.field("m_modifyHealth.m_583C208B"), context))
            _radial_impulse(mech, target, mover, mech.position, horizontal, vertical)
