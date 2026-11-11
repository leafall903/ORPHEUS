"""Selected authoritative consumers for Mercy's 104319 staff and flight."""

import math
from functools import cache

from ow174.game import bots
from ow174.game.mover import embedded_mover_data
from ow174.game.script import expr, graph
from ow174.game.world import UNIT_ENTITY_SCALE

HERO = 0x02E0000000000004
STAFF = 0x006B
HOLD = 5
GUARDIAN = 0x006E
RESURRECT = 0x00CA
VALKYRIE = 0x10DA
GUARDIAN_CONE_SCALE = 9582
CORPSE_MARKER = 653
SEARCHES = {STAFF: (4, HOLD, 51), GUARDIAN: (5, 14), RESURRECT: (14, 38)}
HEAL_EFFECT = 0x04A0000000000019
BOOST_EFFECT = 0x04A0000000000016


@cache
def valkyrie_profile():
    definition = graph.graph(VALKYRIE)
    for row in definition.state(15).field("m_BC5E91CF") or ():
        if row.get("$") == "STU_ED2B22BD":
            return embedded_mover_data(row.get("m_9B7A63EA"))
    raise ValueError("missing Valkyrie mover profile")


def valkyrie_motion(script):
    """Host the active 10DA kind-16 hook and embedded mover profile."""
    if script is None or script.component.owner_dead or script.retired:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != VALKYRIE:
            continue
        hook = instance.states.get(33)
        profile = instance.states.get(15)
        if hook is None or not hook.active or profile is None or not profile.active:
            continue
        cfg = hook.node.field("m_2166C07A")
        scale = 1.0 if cfg is None else expr.to_float(hook.evaluate(cfg, watch=False))
        return valkyrie_profile(), scale
    return None


def query(fight, caster, state):
    """Use authored range and cone with capsule-centre and world occlusion."""
    instance = state.instance
    if fight.match.game_map.free_for_all or fight.is_dead(caster) or caster.gone:
        return ()
    from ow174.game.combat import aim, ray_world

    retain = None
    staff = instance.graph.index == STAFF
    if staff:
        if state.index == HOLD:
            selected = instance.find_var(expr.INSTANCE, 375).value()
            retain = ({item.id for item in selected if isinstance(item, expr.Entity)}
                      if isinstance(selected, tuple) else set())
        else:
            hold = instance.states.get(HOLD)
            if hold is not None and hold.active:
                return ()
    origin = fight._eye(caster)
    forward = aim(caster.mover.yaw, caster.mover.pitch)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    angle_cfg = state.node.field("m_A30B383E")
    angle = expr.to_float(state.evaluate(angle_cfg, watch=False)) if angle_cfg else 360.0
    if staff and angle <= 0 and state.index == 4:
        # Node121 carries the ordinary unscaled acquisition cone.
        angle = expr.to_float(state.evaluate(instance.graph.node(121).field("m_A30B383E"), watch=False))
    threshold = math.cos(math.radians(max(0.0, angle) * 0.5))
    candidates = []
    for target in fight._targets():
        if (target.entity == caster.body or target.team != caster.team
                or (retain is not None and target.entity not in retain)):
            continue
        if instance.graph.index == RESURRECT:
            if not eligible_corpse(fight, target):
                continue
        elif fight.vitals_of(target).dead and (staff or isinstance(target.owner, bots.Bot)):
            continue
        x, y, z = target.feet
        delta = tuple(a - b for a, b in zip((x, y + target.height * 0.5, z), origin, strict=True))
        distance = math.hypot(*delta)
        if not 0 < distance <= radius:
            continue
        direction = tuple(d / distance for d in delta)
        alignment = sum(a * b for a, b in zip(direction, forward, strict=True))
        if angle < 360 and alignment < threshold:
            continue
        wall = ray_world(fight._collision(), origin, direction, distance)
        if wall is None or wall >= distance:
            candidates.append((-alignment, distance, target.entity))
    return (min(candidates)[2],) if candidates else ()


def first_entity(value):
    while isinstance(value, tuple):
        value = value[0] if value else None
    return expr.to_entity(value)


def body_target(fight, identifier):
    return next((t for t in fight._targets() if t.entity == identifier), None)


def eligible_corpse(fight, target):
    return (target is not None and not isinstance(target.owner, bots.Bot)
            and target.owner.body_script is not None and not target.owner.body_script.retired
            and fight.vitals_of(target).dead
            and any(owner is target.owner for _, owner in fight.respawns))


def native(world, cfg, instance):
    """Bind the authored endpoint, body health and the server resurrection branch."""
    cls = cfg.get("$")
    fight = world.match.combat
    if instance.graph.index == RESURRECT:
        if cfg == instance.graph.node(22).field("m_condition"):
            return True
        if cls == "STU_9B7648C3":
            target = body_target(fight, first_entity(instance.evaluate(cfg.get("m_entity"))))
            return fight.vitals_of(target).total if target is not None else 0.0
    if cls not in {"STU_71A87E92", "STU_21A13145", "STU_F7FD94F9", "STU_FDBB0B67", "STU_E72F6348"}:
        return NotImplemented
    target = body_target(fight, first_entity(instance.evaluate(cfg.get("m_entity"))))
    if cls == "STU_E72F6348":
        return target is not None
    if target is None:
        return NotImplemented
    if cls == "STU_71A87E92":
        return expr.Vec3(*target.feet)
    if cls == "STU_21A13145":
        x, y, z = target.feet
        return expr.Vec3(x, y + target.eye, z)
    if cls == "STU_F7FD94F9":
        return expr.f32(target.radius)
    return UNIT_ENTITY_SCALE


def revive(fight, caster, instance, target):
    """Resolve 00CA's engine action against the same eligible player body."""
    if (fight.match.game_map.free_for_all or caster is None or fight.is_dead(caster)
            or caster.gone or fight.is_stunned(caster) or caster.body_script is None
            or caster.body_script.component is not instance.component
            or target is None or target.team != caster.team
            or target.owner is caster or not eligible_corpse(fight, target)):
        return False

    from ow174.game.combat import TICK

    helper = graph.graph(0x00CB)
    if helper is None:
        return False
    immunity = expr.to_float(instance.evaluate(helper.node(1).field("m_timeout")))
    rising = expr.to_float(instance.evaluate(helper.node(3).field("m_timeout")))
    vitals = fight.vitals_of(target)
    for kind, maximum in vitals.most.items():
        if vitals.now[kind] != maximum:
            vitals.now[kind] = maximum
            vitals.changed.add(kind)
    vitals.died_at = vitals.death_tick = vitals.killer = None
    vitals.sleep_until = vitals.sleep_wake_after = 0
    vitals.healing_block_until = vitals.healing_boost_until = vitals.nano_until = vitals.hack_until = 0
    vitals.rising_until = fight.tick + math.ceil(rising / TICK)
    vitals.revive_protection_until = fight.tick + math.ceil(immunity / TICK)
    fight.respawns[:] = [(when, owner) for when, owner in fight.respawns if owner is not target.owner]
    fight._sync_body_health(target.owner, vitals)
    subject = graph.graph(0x01C7)
    if subject is not None:
        for stat in subject.node(29).field("m_stats") or ():
            caster.stats.add(expr.guid_of(stat))
    return True


def recipient(fight, caster, effect_index, effect_guid):
    script = caster.body_script
    if script is None or script.retired or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != STAFF:
            continue
        state = instance.states.get(effect_index)
        hold = instance.states.get(HOLD)
        if (state is None or not state.active or state.ending
                or hold is None or not hold.active or hold.ending):
            continue
        effect = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
        target = state.evaluate(state.node.field("m_targets"), watch=False)
        if (effect == expr.Asset(effect_guid) and isinstance(target, expr.Entity)
                and target.id in query(fight, caster, hold)):
            return state, target.id
    return None


def heal(fight, caster):
    active = recipient(fight, caster, 33, HEAL_EFFECT)
    if active is None:
        return
    state, entity = active
    for param in state.node.field("m_params") or ():
        if expr.guid_of(param.get("m_B5051BCE")) != 0x0A4000000000002A:
            continue
        rate = expr.to_float(state.evaluate(param.get("m_value"), watch=False))
        if rate > 0:
            target = body_target(fight, entity)
            if target is not None:
                fight._heal(caster, target, rate * caster.body_script.frame_ms / 1000.0)
        break


def bonus(fight, shooter):
    """Use the mounted 006C setter at attack settlement or projectile launch."""
    from ow174.game.combat import _source_entity

    if fight.match.game_map.free_for_all:
        return 0.0
    target = _source_entity(shooter)
    for caster in fight.match.players:
        if (caster is shooter or caster.hero.guid != HERO or caster.team != shooter.team
                or not caster.has_body or not caster.spawned or fight.is_stunned(caster)):
            continue
        if graph.effect_script(BOOST_EFFECT) is not None:
            amount = fight.mercy_effects.boost(caster.body, target)
            if amount > 0:
                return amount
            continue
        active = recipient(fight, caster, 35, BOOST_EFFECT)
        if active is None or active[1] != target:
            continue
        definition = graph.graph(0x006C)
        setter = definition.node(19) if definition is not None else None
        if (setter is not None and setter.cls == "STUStatescriptActionSetVar"
                and expr.lvalue(setter.field("m_out_Var")) == (expr.INSTANCE, 174)):
            return max(0.0, expr.to_float(active[0].evaluate(setter.field("m_value"), watch=False)))
    return 0.0
