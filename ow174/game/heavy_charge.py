"""Static host for the manual Heavy's 1408 player capture and wall contact."""

import math

from ow174.game import defenses, projectile_tags
from ow174.game.mover import mover_data
from ow174.game.script import expr

GRAPH = 0x1408
HERO = 0x02E00000000001BA
SEARCH = 96
RECIPIENT = (expr.INSTANCE, 536)
PHASE = (expr.INSTANCE, 59)
EXCLUDED_TAG = 0x0D6000000000052F
BLOCKING_FLAGS = (602, 961, 1859, 2024, 3692, 6601)


def interrupt(fight, bot):
    """End an admitted manual charge on source control or an explicit stop."""
    script = bot.body_script
    if bot.hero != HERO or script is None or not script.component.world.manual_control:
        return
    component = script.component
    for instance in component.instances.values():
        if instance.graph.index == GRAPH and expr.truthy(instance.find_var(*PHASE).value()):
            if 35 in component.held:
                component.edges([(35, False)])
            _end_drive(fight, bot, instance)
            return


def _end_drive(fight, bot, instance):
    driving = bot.body_script.charge_velocity() is not None
    fight.pins.pop(bot.entity, None)
    instance.write(PHASE, 0)
    instance.write(RECIPIENT, None)
    instance.component.run_now()
    if driving:
        bot.velocity = (0.0, bot.velocity[1], 0.0)
        if bot.impulse_mover is not None:
            bot.impulse_mover.velocity = bot.velocity


def refresh_control(world, component):
    if world.hero != HERO or world.match is None or component.entity not in world.match.combat.pins:
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            tracker = instance.states.get(instance.graph.node(SEARCH).state)
            if tracker is not None and tracker.active and not tracker.ending and tracker.targets:
                return
    world.match.combat.pins.pop(component.entity, None)


def flag(target, key):
    script = target.owner.body_script
    slot = script.component.entity_var(key, create=False) if script is not None else None
    return slot is not None and expr.truthy(slot.value())


def eligible(fight, source, target):
    return (getattr(target.owner, "mover", None) is not None
            and not target.owner.gone
            and not defenses.invulnerable(target.owner)
            and defenses.fortify_scale(target.owner) is None
            and target.owner not in fight.zarya_barriers.enemies(source))


def search(fight, state):
    from ow174.game.combat import EYE, aim, capsule_visible, segment_distance

    source = fight._npc_attack_source(state)
    if source is None or fight.match.ended:
        return ()
    enemies = fight._enemies(source)
    victim = fight.pins.get(source.entity)
    if victim is not None:
        return (expr.Entity(victim),) if any(t.entity == victim and eligible(fight, source, t)
                                             for t in enemies) else ()
    if state.var is not None:
        # Initial side damage can break a barrier after the first search.
        # A repeat poll in that same frame still belongs to the initial contact.
        frame_ms = state.instance.component.frame_ms
        if state.now // frame_ms == state.start // frame_ms:
            return ()
        # A retained graph recipient prevents reacquisition after interruption.
        if isinstance(state.instance.find_var(*RECIPIENT).value(), expr.Entity):
            return ()

    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    cone = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    if not isinstance(offset, expr.Vec3) or radius <= 0:
        return ()
    fx, _, fz = aim(source.yaw, 0)
    x, y, z = source.position
    centre = (x + offset.x * fz + offset.z * fx, y + offset.y, z - offset.x * fx + offset.z * fz)
    origin = (x, y + EYE, z)
    cosine = math.cos(math.radians(cone) / 2)
    for target in sorted(enemies, key=lambda t: math.dist(source.position, t.feet)):
        if not eligible(fight, source, target) or target.entity in fight.pins.values():
            continue
        # Known player bodies stand in for the native component/filter profile.
        tags = projectile_tags.tags_of(target.owner.hero.body)
        if tags is None or EXCLUDED_TAG in tags or any(flag(target, key) for key in BLOCKING_FLAGS):
            continue
        tx, _, tz = aim(target.owner.mover.yaw, 0)
        if flag(target, 596) and fx * tx + fz * tz < 0:
            continue
        dx, dz = target.feet[0] - x, target.feet[2] - z
        if dx * fx + dz * fz < cosine * math.hypot(dx, dz):
            continue
        a, b = target.segment()
        if (segment_distance(centre, a, b) <= radius + target.radius
                and capsule_visible(fight._collision(), origin, a, b, target.radius)):
            fight.pins[source.entity] = target.entity
            fight._interrupt(target)
            return (expr.Entity(target.entity),)
    return ()


def move_victim(fight, bot, instance, tick, seconds, blocked):
    victim = fight.pins.get(bot.entity)
    if victim is None:
        return
    tracker = instance.states.get(instance.graph.node(SEARCH).state)
    target = next((t for t in fight._enemies(bot) if t.entity == victim), None)
    velocity = bot.body_script.charge_velocity()
    if (fight.match.ended or tracker is None or not tracker.active or tracker.ending or velocity is None
            or target is None or not eligible(fight, bot, target) or fight.is_stunned(bot)):
        fight.pins.pop(bot.entity, None)
        return
    if blocked is None:
        return
    speed = math.hypot(velocity[0], velocity[2])
    if not speed:
        fight.pins.pop(bot.entity, None)
        return
    fx, fz = velocity[0] / speed, velocity[2] / speed
    gap = mover_data(bot.body).stand_radius + target.radius + 0.02
    x, y, z = bot.position
    destination = (x + gap * fx, y, z + gap * fz)
    mover = target.owner.mover
    ahead = (target.feet[0] - x) * fx + (target.feet[2] - z) * fz
    # Let the moving Heavy close the gap first. Never pull the victim backward
    # on acquisition or advance it faster than the charge vector.
    if not blocked and ahead <= gap:
        mover.use(world=fight._collision())
        blocked = mover._move_to(seconds, destination, speed) is False
        history = fight.history.get(victim)
        if history and history[-1][0] == tick:
            history[-1] = (tick, tuple(mover.position), target.height, target.eye)
        if mover.history and mover.history[-1].frame == tick:
            mover.history[-1] = mover.snapshot(tick)
    if not blocked:
        return

    # Static mover obstruction supplies the wall impact. Read the authored
    # damage payload without forcing the graph's separate counter-charge branch.
    payload = instance.graph.node(29).field("m_modifyHealth")
    amount = expr.to_float(instance.evaluate(payload["m_amount"]))
    loadout = instance.evaluate(payload["m_loadout"])
    _end_drive(fight, bot, instance)
    fight._damage(bot, target, amount, False,
                  loadout.guid if isinstance(loadout, expr.Asset) else None, tick)
